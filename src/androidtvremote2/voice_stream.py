"""High-level voice streaming session wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING

from androidtvremote2.const import LOGGER

if TYPE_CHECKING:
    from types import TracebackType

    from androidtvremote2.remote import RemoteProtocol


class VoiceStream:
    """High-level voice streaming session wrapper.

    Obtained from AndroidTVRemote.start_voice().
    """

    def __init__(self, proto: RemoteProtocol, session_id: int) -> None:
        """Initialize.

        param proto: RemoteProtocol instance.
        param session_id: voice session id.
        """
        self._proto = proto
        self.session_id = session_id
        self._closed = False

    def send_chunk(self, chunk: bytes) -> bool:
        """Send a chunk of audio data.

        - The audio data must be 16-bit PCM, mono, 8000 Hz.
        - The chunk size should be at least 8 KB. Smaller chunks will be padded with zeros.
        - Chunk sizes larger than 20 KB will be split into multiple chunks.

        :param chunk: The PCM audio data chunk to be sent. Should be a multiple of 8 KB.
        :return: False if a voice session is closed.
        :raises ConnectionClosed: If the connection is lost.
        """
        if self._closed:
            LOGGER.debug("VoiceStream already closed")
            return False
        self._proto.send_voice_chunk(chunk, self.session_id)
        return True

    def end(self) -> None:
        """End the voice stream."""
        if not self._closed:
            self._proto.end_voice(self.session_id)
            self._closed = True

    async def __aenter__(self) -> VoiceStream:
        """Enter async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """End the asynchronous context manager session.

        This method implements the asynchronous exit for a context manager. It is called
        when execution leaves the `async with` block that the instance of the class
        is managing.

        :param exc_type: The exception type raised within the context block if an exception occurred.
        :param exc: The exception object raised within the context block if an exception was raised.
        :param tb: The traceback object associated with the raised exception, if any.
        """
        self.end()
