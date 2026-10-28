# Bench setup — Ubuntu laptop (PigO BE line laptop, on loan)

The PigO Belgium line laptop (`pigobpg1`) is used for the first camera tests. Everything
here is **additive and reversible** so the laptop goes back to the line unchanged:

- the camera gets its **own NetworkManager profile** (`basler-cam`), never the line's;
- milof runs in its **own conda environment**, never PigO's `base`;
- "Before the laptop returns" at the end undoes it all.

Hardware check (done): `enp0s31f6` = Intel I219-LM, supports 1000baseT/Full. Wi-Fi
(`wlp0s20f3`) stays the internet connection.

## 1. Look, don't touch: the existing network profiles

```bash
nmcli connection show
```

Note which profile is bound to `enp0s31f6` (the line's RFID/KillPig network in Belgium).
Do **not** edit or delete it.

## 2. Camera network profile

```bash
sudo nmcli connection add type ethernet ifname enp0s31f6 con-name basler-cam \
  ipv4.method manual ipv4.addresses 192.168.10.1/24 ipv4.never-default yes \
  ipv6.method disabled 802-3-ethernet.mtu 9000 connection.autoconnect no
```

- `never-default yes`: internet keeps going over Wi-Fi.
- `autoconnect no`: it only comes up when you ask, so it never fights the line profile.

Bring it up when the camera is connected, down when done:

```bash
sudo nmcli connection up basler-cam
sudo nmcli connection down basler-cam
```

## 3. Receive buffers (per boot, nothing permanent)

GigE cameras send bursts; small kernel buffers drop packets.

```bash
sudo sysctl -w net.core.rmem_max=33554432 net.core.rmem_default=33554432
sudo ethtool -G enp0s31f6 rx 4096
```

## 4. Install Basler pylon

1. Download **pylon Software Suite for Linux x86 (64 bit)**, `.deb` package, from
   baslerweb.com → Downloads → Software.
2. Install:
   ```bash
   sudo apt install ./pylon_*.deb
   ```
3. Tools are in `/opt/pylon/bin/`: `pylonviewer`, `ipconfigurator`.

## 5. Connect the camera

1. PoE injector to mains.
2. Injector `P+DATA OUT` → camera. Injector `DATA IN` → laptop Ethernet port.
3. `sudo nmcli connection up basler-cam`
4. Check the link:
   ```bash
   sudo ethtool enp0s31f6 | grep -E "Speed|Link detected"   # Speed: 1000Mb/s, Link detected: yes
   ```

## 6. Give the camera its IP

```bash
/opt/pylon/bin/ipconfigurator
```

The camera appears (possibly with a 169.254.x.x address). Set a **persistent IP**
`192.168.10.2`, mask `255.255.255.0`, save. The camera reboots.

If it does not appear: Wi-Fi VPN off, `sudo ufw status` (if active:
`sudo ufw allow from 192.168.10.0/24`), cable in the right injector port.

## 7. First image in pylon Viewer

```bash
/opt/pylon/bin/pylonviewer
```

1. Open the camera; check the device name reads `r2L4096-14gc`.
2. Set packet size (`GevSCPSPacketSize`) to 9000.
3. Line rate ~3000, exposure low; **Continuous shot**.
4. A line-scan camera only shows a proper picture of something **moving** past it: slide
   a printed page or a hand across the scan line. A static scene shows vertical streaks;
   that is normal.
5. Focus at the working distance, set aperture to ~f/4, lock the lens rings.

## 8. milof in its own environment

```bash
conda create -n milof python=3.11 -y
conda activate milof
git clone https://github.com/coopersh/milof.git ~/repositories/milof
cd ~/repositories/milof
pip install -e ".[camera,dev]"
pytest tests/
python -m acquisition capture --mode tiles --seconds 10
ls data/*/images | head
```

## Before the laptop returns to Belgium

```bash
sudo nmcli connection delete basler-cam
conda env remove -n milof          # optional
nmcli connection show              # line profile is still there, unchanged
```

The sysctl/ethtool settings from step 3 reset on reboot. pylon can stay installed or be
removed with `sudo apt remove pylon`.
