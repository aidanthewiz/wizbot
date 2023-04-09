import glob
import logging
import threading
import time

import colorlog
import serial
from evdev import InputDevice, list_devices, ecodes
from serial.serialutil import SerialException

SABERTOOTH_ADDRESS = 128

CONTROLLER_NAME = "8BitDo SN30 Pro+"
SABERTOOTH_SERIAL_PORTS = ('/dev/ttyACM*', '/dev/ttyUSB*')
NIGHT_MODE = True
MAX_SPEED = 126

# Properly Tuned
DEAD_ZONE = 5

# Set up the logging formatter
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

# Set up the logging handlers to use the custom formatter
handler = logging.StreamHandler()
handler.setFormatter(formatter)
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger().addHandler(handler)

sabertooth_logger = logging.getLogger("Sabertooth")
raspberry_pi_logger = logging.getLogger("RaspberryPi")


def find_controller():
    devices = [InputDevice(fn) for fn in list_devices()]
    for device in devices:
        if device.name == CONTROLLER_NAME:
            return device
    return None


def ungrab_controller(controller):
    try:
        controller.ungrab()
    except OSError as e:
        if e.errno == 19:
            raspberry_pi_logger.warning("Controller already disconnected.")
        else:
            raise


def send_packet(ser, address, command, value):
    if NIGHT_MODE and value > 20:
        value = 20
        raspberry_pi_logger.debug(f"NIGHT MODE")

    checksum = (address + command + value) & 0x7F
    packet = bytes([address, command, value, checksum])

    if value != 0:
        raspberry_pi_logger.debug(f"Command ID: {command}, Motor Speed: {value}")
        raspberry_pi_logger.debug(f"Sending packet: {packet}, Calculated Checksum: {checksum}")

    try:
        ser.write(packet)
        ser.flush()

        return True
    except SerialException as e:
        raspberry_pi_logger.error(f"Error sending packet to Sabertooth: {e}")
        return False


def handle_event(event, motor_speeds):
    if event.type == ecodes.EV_ABS:
        if event.code in (ecodes.ABS_Y, ecodes.ABS_RY):
            motor_speed = int(((event.value - 32767) / 32767) * 126)

            if abs(motor_speed) < DEAD_ZONE:
                motor_speed = 0

            motor_speeds[0 if event.code == ecodes.ABS_Y else 1] = motor_speed


def send_motor_speeds(ser, motor_speeds):
    for i, motor_speed in enumerate(motor_speeds):
        if motor_speed is not None:
            if i == 1:  # Right motor (ABS_RY)
                command = 0 if motor_speed >= 0 else 1
            else:  # Left motor (ABS_Y)
                command = 5 if motor_speed >= 0 else 4
            success = send_packet(ser, SABERTOOTH_ADDRESS, command, abs(motor_speed))

            if not success:
                return False
    return True


def motor_speed_sender(ser, motor_speeds, stop_event):
    while not stop_event.is_set():
        if not all(speed is None for speed in motor_speeds):
            success = send_motor_speeds(ser, motor_speeds)
            if not success:
                break
        else:
            time.sleep(0.01)


def process_controller_events(controller, motor_speeds, stop_event):
    while not stop_event.is_set():
        try:
            for event in controller.read_loop():
                handle_event(event, motor_speeds)
        except OSError as e:
            if e.errno == 19:
                raspberry_pi_logger.warning("Controller disconnected.")
                break
            else:
                raise


def find_sabertooth_port():
    sabertooth_ports = []
    for port_pattern in SABERTOOTH_SERIAL_PORTS:
        sabertooth_ports.extend(glob.glob(port_pattern))
    return sabertooth_ports[0] if sabertooth_ports else None


def connect_sabertooth():
    sabertooth_port = find_sabertooth_port()
    if sabertooth_port:
        try:
            return serial.Serial(sabertooth_port, 115200, timeout=0.01)
        except serial.SerialException as e:
            raspberry_pi_logger.warning(f"Unable to connect to Sabertooth: {e}")
    return None


def sabertooth_serial_reader(ser):
    while ser.is_open:
        try:
            sabertooth_output = ser.readline().decode('utf-8').rstrip()
            if sabertooth_output:
                sabertooth_logger.info(sabertooth_output)
        except SerialException as e:
            raspberry_pi_logger.error(f"Error reading Sabertooth log: {e}")
            break


def main():
    controller = None
    ser = None
    motor_speeds = [0, 0]
    stop_event = threading.Event()

    while True:
        try:
            if not controller:
                controller = find_controller()

                if controller:
                    raspberry_pi_logger.info("Controller connected")
                    controller.grab()
                else:
                    raspberry_pi_logger.warning("Controller not found. Retrying in 5 seconds.")
                    time.sleep(5)
                    continue

            if not ser:
                ser = connect_sabertooth()
                if ser:
                    raspberry_pi_logger.info("Sabertooth connected")
                    sabertooth_log_thread = threading.Thread(target=sabertooth_serial_reader, args=(ser,))
                    sabertooth_log_thread.daemon = True
                    sabertooth_log_thread.start()

                    motor_speed_sender_thread = threading.Thread(target=motor_speed_sender,
                                                                 args=(ser, motor_speeds, stop_event))
                    motor_speed_sender_thread.daemon = True
                    motor_speed_sender_thread.start()
                else:
                    raspberry_pi_logger.warning("Sabertooth not found. Retrying in 5 seconds.")
                    time.sleep(5)
                    continue

            if controller and ser:
                event_thread = threading.Thread(target=process_controller_events,
                                                args=(controller, motor_speeds, stop_event))
                event_thread.daemon = True
                event_thread.start()

                while event_thread.is_alive():
                    time.sleep(0.01)
            else:
                time.sleep(0.01)

            ungrab_controller(controller)
            controller = None

        except KeyboardInterrupt:
            raspberry_pi_logger.warning("Exiting due to keyboard interrupt")
            stop_event.set()
            ungrab_controller(controller)
            if ser:
                ser.close()
            break

        except (OSError, serial.SerialException) as e:
            raspberry_pi_logger.error(f"Error in communication with Sabertooth: {e}")
            if ser:
                ser.close()
                ser = None
            time.sleep(5)

        except Exception as e:
            raspberry_pi_logger.error(f"Unhandled exception: {e}")
            ungrab_controller(controller)
            if ser:
                ser.close()
            break


if __name__ == "__main__":
    main()
