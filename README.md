# androidtvremote2

A Python library for interacting with Android TV using the Android TV Remote protocol v2. This is the same protocol the Google TV mobile app is using. It doesn't require ADB or enabling developer tools on the Android TV device. It only requires the [Android TV Remote Service](https://play.google.com/store/apps/details?id=com.google.android.tv.remote.service) that comes pre-installed on most Android TV devices.

For a list of the most common commands you can send to the Android TV see: [TvKeys](https://github.com/tronikos/androidtvremote2/blob/main/TvKeys.txt).
For a full list see [here](https://github.com/tronikos/androidtvremote2/blob/b4c49ac03043b1b9c40c2f2960e466d5a3b8bd67/src/androidtvremote2/remotemessage.proto#L90).
In addition to commands you can send URLs to open apps registered to handle them. See [this guide](https://community.home-assistant.io/t/android-tv-remote-app-links-deep-linking-guide/567921) for how to find deep links for apps.

## Credits

- Official [implementation](https://android.googlesource.com/platform/external/google-tv-pairing-protocol/+/refs/heads/master) of the pairing protocol in Java
- [Implementation](https://github.com/farshid616/Android-TV-Remote-Controller-Python) in Python but for the old v1 protocol
- [Implementation](https://github.com/louis49/androidtv-remote) in Node JS for the v2 protocol
- [Description](https://github.com/Aymkdn/assistant-freebox-cloud/wiki/Google-TV-(aka-Android-TV)-Remote-Control-(v2)) of the v2 protocol

## Example

See [demo.py](https://github.com/tronikos/androidtvremote2/blob/main/src/demo.py)

## Development environment

```sh
python3 -m venv .venv
source .venv/bin/activate
# for Windows CMD:
# .venv\Scripts\activate.bat
# for Windows PowerShell:
# .venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install --upgrade pip
python -m pip install .

# Generate *_pb2.py from *.proto
python -m pip install grpcio-tools mypy-protobuf
python -m grpc_tools.protoc src/androidtvremote2/*.proto --python_out=src/androidtvremote2 --mypy_out=src/androidtvremote2 -Isrc/androidtvremote2

# Run pre-commit
python -m pip install pre-commit
pre-commit autoupdate
pre-commit install
pre-commit run --all-files

# Alternative: run formatter, lint, and type checking
python -m pip install isort black flake8 ruff mypy
isort . ; black . ; flake8 . ; ruff check . --fix ; mypy --install-types .

# Run tests
python -m pip install pytest
pytest

# Run demo
python -m pip install pynput zeroconf
python src/demo.py

# Build package
python -m pip install build
python -m build
```
