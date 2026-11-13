# Solo Miner Desktop Widget

A small, always-on-top macOS desktop widget for home Bitcoin miners that run AxeOS (the firmware family used by Bitaxe and NerdAxe-type miners).

It reads each miner's local `http://<ip>/api/system/info` and shows:

- seven-segment hashrate, power and efficiency (J/TH)
- a temperature gauge and fan speed
- a one-hour hashrate chart
- accepted/rejected shares, best difficulty and uptime
- a toggle between miners, plus a combined view

| One miner | All miners |
| --- | --- |
| ![One miner](docs/screenshot-miner.png) | ![All miners](docs/screenshot-all.png) |

The screenshots show sample data, not anyone's live miner address.

## How it works

- `widget/miner_widget_server.py` is a tiny Python server (standard library only) on `localhost:8757`. It polls your miners and serves the page and a JSON feed.
- `widget/miner_widget.html` is the widget page. It loads nothing from the internet: no external scripts, fonts, images or requests. The only network traffic is the server asking your own miners for their status.
- `widget/miner_widget_window.swift` is a small Swift window (WebKit) that shows the page, floating above other windows.
- Two LaunchAgents start the server and the window at login.

## Tested on

Tested only on a NerdOCTaxe. Other AxeOS miners should work because they expose the same `/api/system/info`, but they are untested.

## Requirements

- macOS with the Xcode Command Line Tools (`xcode-select --install`) for `swiftc`
- `python3` (ships with the Command Line Tools; the system `python3` is used)
- A miner on your local network running AxeOS

## Install

```sh
git clone <this repository>
cd solo-miner-desktop-widget
./install.sh
```

`install.sh` checks for macOS, `swiftc` and `python3`, compiles the Swift window, creates `widget/miners.json` from the example if it does not exist, writes two LaunchAgents into `~/Library/LaunchAgents` (`com.solominer.widget-server` and `com.solominer.widget-window`) using your home folder and this folder's path, and loads them. Do not move the folder afterwards without running `./install.sh` again.

## Configure your miners

Edit `widget/miners.json`:

```json
[
  {"name": "Miner 1", "host": "192.168.1.50"},
  {"name": "Miner 2", "host": ""}
]
```

`host` is the miner's IP address on your network (you can also include a port, like `192.168.1.50:8080`). An empty host shows "not plugged in yet". The file is read on every refresh, so changes show up within about 30 seconds.

### Showing "your node"

If your miner points at your own Bitcoin node, you can have the widget say so. Use the object form of the file and list the stratum host(s) of your node:

```json
{
  "miners": [
    {"name": "Miner 1", "host": "192.168.1.50"}
  ],
  "my_node_hosts": ["192.168.1.10"]
}
```

When a miner's reported stratum host is in `my_node_hosts`, the pool field shows "your node". Otherwise it shows the pool host name the miner reports, and "backup pool" when the miner is on its fallback pool. `my_node_hosts` is optional.

### Environment variables

- `MINER_CONFIG`: path to the config file (default `widget/miners.json`)
- `MINER_PORT`: server port (default `8757`). `install.sh` does not read it. If you change it, edit both plists in `~/Library/LaunchAgents`: add `MINER_PORT` under `EnvironmentVariables` in `com.solominer.widget-server.plist`, and change the URL in `com.solominer.widget-window.plist`. Then reload both agents.

## macOS Local Network permission

Background programs cannot reach devices on your home network until you allow it once. If the widget shows "not answering" even though the miner's own web page loads in your browser, open System Settings > Privacy & Security > Local Network and allow the Python program (the widget server). Then restart the server:

```sh
launchctl kickstart -k gui/$(id -u)/com.solominer.widget-server
```

## Using it

- Move it: click and drag anywhere on the widget (not on the buttons). It remembers its position.
- Switch: the buttons at the bottom pick a miner or the combined view. The choice is remembered.

## Uninstall

```sh
./uninstall.sh
```

This unloads the two LaunchAgents and removes only the two plist files that `install.sh` installed (`com.solominer.widget-server.plist` and `com.solominer.widget-window.plist`). Your `widget/miners.json` and this folder are left alone.

## Security

The local server only answers requests addressed to localhost.

## Tests

```sh
python3 -m unittest discover -s tests
```

## Troubleshooting

- Widget does not appear: check that `swiftc` is installed and re-run `./install.sh`.
- "Not answering": check the miner's IP address, that `http://<ip>/api/system/info` opens in a browser, and the Local Network permission above.
- "Miner list could not be read": `widget/miners.json` is not valid JSON.
- Server errors are written to `widget/server.log`.

## License

MIT, see [LICENSE](LICENSE).

Built by Stelar Digital · stelardigital.com
