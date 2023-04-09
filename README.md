# WizBot

## Raspberry Pi Setup:

* sudo apt update && sudo apt full-upgrade -y && sudo apt autoremove -y
* pip3 install pyserial evdev colorlog
* sudo apt install evtest
* sudo bluetoothctl
* scan on
* pair {MAC_ADDRESS}
* trust {MAC_ADDRESS}
* connect {MAC_ADDRESS}
* scan off

### Speed up boot time:

* sudo nano /boot/config.txt
    * disable_splash=1
    * boot_delay=0

* sudo nano /boot/cmdline.txt
    * Add quiet parameter to end

* systemd-analyze blame
    * Check for slow services

## Sabertooth 2x32 DIP:

1) Off
2) On
3) On
4) Off
5) Off
6) Off

## 8BitDo Controller Mappings:

* ABS_RY+
    * Sabertooth Command 0
* ABS_RY-
    * Sabertooth Command 1
* ABS_Y-
    * Sabertooth Command 4
* ABS_Y+
    * Sabertooth Command 5
* EV_KEY
    * Emergency Stop
