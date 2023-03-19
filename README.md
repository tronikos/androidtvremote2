# androidtvremote2

A Python library for interacting with Android TV using the Android TV Remote protocol v2. This is the same protocol the Google TV app is using. It doesn't require ADB or enabling developer tools on the Android TV device.

For a list of the most common commands you can send to the Android TV see: [TvKeys](https://github.com/tronikos/androidtvremote2/blob/main/TvKeys.txt).
For a full list see [here](https://github.com/tronikos/androidtvremote2/blob/main/src/androidtvremote2/remotemessage.proto#L90).
In addition to commands you can send URLs to open apps registered to handle them.

## Credits

- Official [implementation](https://android.googlesource.com/platform/external/google-tv-pairing-protocol/+/refs/heads/master) of the pairing protocol in Java
- [Implementation](https://github.com/farshid616/Android-TV-Remote-Controller-Python) in Python but for the old v1 protocol
- [Implementation](https://github.com/louis49/androidtv-remote) in Node JS for the v2 protocol
- [Description](https://github.com/Aymkdn/assistant-freebox-cloud/wiki/Google-TV-(aka-Android-TV)-Remote-Control-(v2)) of the v2 protocol

## Example

See [demo.py](https://github.com/tronikos/androidtvremote2/blob/main/demo.py)

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
python -m pip install grpcio-tools
python -m grpc_tools.protoc src/androidtvremote2/*.proto --python_out=src/androidtvremote2 -Isrc/androidtvremote2

# Run formatter
python -m pip install isort black
isort .
black .

# Run lint
python -m pip install flake8 ruff
flake8 .
ruff .

# Run tests
python -m pip install pytest
pytest

# Run demo
python -m pip install pynput zeroconf
python demo.py

# Build package
python -m pip install build
python -m build
```
