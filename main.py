import configparser
import glob
import logging
import threading
from typing import List, Tuple, Optional

import colorlog
import serial
from evdev import InputDevice, list_devices, ecodes, InputEvent
from serial.serialutil import SerialException

config = configparser.ConfigParser()
config.read('config.ini')

CONTROLLER_NAME: str = config.get('Settings', 'CONTROLLER_NAME')
SABERTOOTH_ADDRESS: int = config.getint('Settings', 'SABERTOOTH_ADDRESS')
SABERTOOTH_SERIAL_PORTS: List[str] = config.get('Settings', 'SABERTOOTH_SERIAL_PORTS').split(', ')
NIGHT_MODE: bool = config.getboolean('Settings', 'NIGHT_MODE')
MAX_SPEED: int = config.getint('Settings', 'MAX_SPEED')
DEAD_ZONE: int = config.getint('Settings', 'DEAD_ZONE')


def init_logger() -> Tuple[logging.Logger, logging.Logger]:
    formatter = colorlog.ColoredFormatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(log_color)s%(message)s",
        datefmt=None,
        reset=True,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        },
        secondary_log_colors={
            'message': {
                'Sabertooth': 'white,bg_blue',
                'RaspberryPi': 'white,bg_magenta',
            }
        },
        style='%'
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logging.getLogger().setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)

    return logging.getLogger("Sabertooth"), logging.getLogger("RaspberryPi")


sabertooth_logger, raspberry_pi_logger = init_logger()


def find_controller() -> Optional[InputDevice]:
    devices = [InputDevice(fn) for fn in list_devices()]
    for device in devices:
        if device.name == CONTROLLER_NAME:
            return device
    return None


def ungrab_controller(controller: InputDevice) -> None:
    try:
        controller.ungrab()
    except OSError as e:
        if e.errno == 19:
            raspberry_pi_logger.warning("Controller already disconnected.")
        else:
            raise


def emergency_shutoff(ser: serial.Serial, emergency_stop: threading.Event, motor_speeds: List[int]) -> bool:
    emergency_stop.set()
    motor_speeds[0] = motor_speeds[1] = 0
    command0 = send_packet(ser, SABERTOOTH_ADDRESS, 0, 0, emergency_stop)
    command5 = send_packet(ser, SABERTOOTH_ADDRESS, 5, 0, emergency_stop)
    return command0 and command5


def send_packet(ser: serial.Serial, address: int, command: int, value: int, emergency_stop: threading.Event) -> bool:
    if NIGHT_MODE and value > 20:
        value = 20
        raspberry_pi_logger.debug(f"NIGHT MODE")

    checksum = (address + command + value) & 0x7F
    packet = bytearray(4)
    packet[0] = address
    packet[1] = command
    packet[2] = value
    packet[3] = checksum

    if value != 0:
        raspberry_pi_logger.debug(f"Command ID: {command}, Motor Speed: {value}")
        raspberry_pi_logger.debug(f"Sending packet: {packet}, Calculated Checksum: {checksum}")

    if emergency_stop.is_set():
        raspberry_pi_logger.info("EMERGENCY STOP")
        if value != 0:
            return True

    try:
        ser.write(packet)
        ser.flush()

        return True
    except SerialException as e:
        raspberry_pi_logger.error(f"Error sending packet to Sabertooth: {e}")
        return False


def handle_event(event: InputEvent, motor_speeds: List[int], ser: serial.Serial,
                 emergency_stop: threading.Event) -> None:
    if emergency_stop.is_set():
        return

    if event.type == ecodes.EV_ABS:
        if event.code in (ecodes.ABS_Y, ecodes.ABS_RY):
            motor_speed = int(((event.value - 32767) / 32767) * 126)

            if abs(motor_speed) < DEAD_ZONE:
                motor_speed = 0

            motor_speeds[0 if event.code == ecodes.ABS_Y else 1] = motor_speed

    # Add handling for other event types and codes
    elif event.type == ecodes.EV_SYN:
        pass  # Event type 0 (EV_SYN)
    elif event.type == ecodes.EV_KEY:
        if event.code == 139:  # KEY_MENU
            if event.value == 1:  # Key press event
                emergency_shutoff(ser, emergency_stop, motor_speeds)  # Emergency shutoff
        elif event.code in range(304, 314):  # BTN_SOUTH to BTN_TR2
            pass
    elif event.type == ecodes.EV_ABS:
        if event.code in (ecodes.ABS_X, ecodes.ABS_Y, ecodes.ABS_Z,
                          ecodes.ABS_RX, ecodes.ABS_RY, ecodes.ABS_RZ,
                          ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y):
            pass
    elif event.type == ecodes.EV_MSC:
        if event.code == ecodes.MSC_SCAN:  # MSC_SCAN
            pass
    elif event.type == 21:  # EV_FF
        if event.code in range(80, 97):  # FF_RUMBLE to FF_GAIN
            pass


def send_motor_speeds(ser: serial.Serial, motor_speeds: List[int], emergency_stop: threading.Event) -> bool:
    for i, motor_speed in enumerate(motor_speeds):
        if motor_speed is not None:
            if i == 1:  # Right motor (ABS_RY)
                command = 0 if motor_speed >= 0 else 1
            else:  # Left motor (ABS_Y)
                command = 5 if motor_speed >= 0 else 4
            success = send_packet(ser, SABERTOOTH_ADDRESS, command, abs(motor_speed), emergency_stop)

            if not success:
                return False
    return True


def motor_speed_sender(ser: serial.Serial, motor_speeds: List[int], communication_stop: threading.Event,
                       emergency_stop: threading.Event) -> None:
    while not communication_stop.is_set():
        if emergency_stop.is_set():
            motor_speeds[0] = motor_speeds[1] = 0  # Set motor speeds to 0

        if any(speed is not None for speed in motor_speeds):
            success = send_motor_speeds(ser, motor_speeds, emergency_stop)
            if not success:
                break
        communication_stop.wait(0.01)


def process_controller_events(controller: InputDevice, motor_speeds: List[int], ser: serial.Serial,
                              communication_stop: threading.Event, emergency_stop: threading.Event) -> None:
    while not communication_stop.is_set():
        try:
            for event in controller.read_loop():
                handle_event(event, motor_speeds, ser, emergency_stop)
        except OSError as e:
            if e.errno == 19:
                raspberry_pi_logger.warning("Controller disconnected.")
                emergency_shutoff(ser, emergency_stop, motor_speeds)
                break
            else:
                raise


def find_sabertooth_port() -> Optional[str]:
    sabertooth_ports = []
    for port_pattern in SABERTOOTH_SERIAL_PORTS:
        sabertooth_ports.extend(glob.glob(port_pattern))
    return sabertooth_ports[0] if sabertooth_ports else None


def connect_sabertooth() -> Optional[serial.Serial]:
    sabertooth_port = find_sabertooth_port()
    if sabertooth_port:
        try:
            return serial.Serial(sabertooth_port, 115200, timeout=0.01)
        except serial.SerialException as e:
            raspberry_pi_logger.warning(f"Unable to connect to Sabertooth: {e}")
    return None


def sabertooth_serial_reader(ser: serial.Serial, communication_stop: threading.Event) -> None:
    while not communication_stop.is_set():
        try:
            sabertooth_output = ser.readline().decode('utf-8').rstrip()
            if sabertooth_output:
                sabertooth_logger.info(sabertooth_output)
        except SerialException as e:
            raspberry_pi_logger.error(f"Error reading Sabertooth log: {e}")
            break
        communication_stop.wait(0.01)


def main() -> None:
    controller: Optional[InputDevice] = None
    ser: Optional[serial.Serial] = None
    motor_speeds = [0, 0]
    communication_stop = threading.Event()
    emergency_stop = threading.Event()

    while True:
        try:
            if not controller:
                controller = find_controller()

                if controller:
                    raspberry_pi_logger.info("Controller connected")
                    controller.grab()
                else:
                    raspberry_pi_logger.warning("Controller not found. Retrying in 1 second.")
                    communication_stop.wait(1)
                    continue

            if not ser:
                ser = connect_sabertooth()
                if ser:
                    raspberry_pi_logger.info("Sabertooth connected")
                    sabertooth_log_thread = threading.Thread(target=sabertooth_serial_reader,
                                                             args=(ser, communication_stop))
                    sabertooth_log_thread.daemon = True
                    sabertooth_log_thread.start()

                    motor_speed_sender_thread = threading.Thread(target=motor_speed_sender,
                                                                 args=(
                                                                     ser, motor_speeds, communication_stop,
                                                                     emergency_stop))
                    motor_speed_sender_thread.daemon = True
                    motor_speed_sender_thread.start()
                else:
                    raspberry_pi_logger.warning("Sabertooth not found. Retrying in 1 second.")
                    communication_stop.wait(1)
                    continue

            if controller and ser:
                event_thread = threading.Thread(target=process_controller_events,
                                                args=(
                                                    controller, motor_speeds, ser, communication_stop, emergency_stop))
                event_thread.daemon = True
                event_thread.start()

                while event_thread.is_alive():
                    communication_stop.wait(0.01)
            else:
                communication_stop.wait(0.01)

            ungrab_controller(controller)
            controller = None

        except KeyboardInterrupt:
            raspberry_pi_logger.warning("Exiting due to keyboard interrupt")
            communication_stop.set()
            ungrab_controller(controller)
            if ser:
                ser.close()
            break

        except (OSError, serial.SerialException) as e:
            raspberry_pi_logger.error(f"Error in communication with Sabertooth: {e}")
            if ser:
                ser.close()
                ser = None
            communication_stop.wait(5)

        except Exception as e:
            raspberry_pi_logger.error(f"Unhandled exception: {e}")
            ungrab_controller(controller)
            if ser:
                ser.close()
            break


if __name__ == "__main__":
    main()
