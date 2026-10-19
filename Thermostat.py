#!/usr/bin/env python3
"""CS 350 thermostat prototype. Verify BCM pin configuration against YOUR circuit.

Install dependencies in the Raspberry Pi environment:
  python3 -m pip install gpiozero adafruit-blinka adafruit-circuitpython-ahtx0 RPLCD pyserial
Enable I2C and UART (with serial login shell disabled) in Raspberry Pi configuration.

WIRING: BCM17 mode button is an assumption; change it to match your existing circuit.
BCM25 = setpoint UP, BCM12 = DOWN, BCM18 red PWM LED, BCM23 blue PWM LED.
LCD PIN VALUES BELOW ARE EXAMPLES ONLY, NOT VERIFIED FROM YOUR LAB TEMPLATE.
The provided guide does NOT specify the original LCD pin mapping or serial baud rate.
Check your existing Thermostat.py / LCD lab before running on real hardware.
"""
from __future__ import annotations

import logging
import signal
import time
from datetime import datetime
from enum import Enum
from threading import Lock

import board
import adafruit_ahtx0
import serial
from gpiozero import Button, PWMLED
from RPLCD.gpio import CharLCD
from RPi import GPIO

# BCM numbering (NOT physical pin numbers)
MODE_BUTTON_PIN = 17           # VERIFY with pre-existing circuit!
INCREASE_BUTTON_PIN = 25      # Lab guide: GPIO25
DECREASE_BUTTON_PIN = 12      # Lab guide: GPIO12
RED_LED_PIN = 18               # VERIFY; previous CS 350 circuit
BLUE_LED_PIN = 23              # VERIFY; previous CS 350 circuit
# EXAMPLE LCD wiring: change to the pins used by YOUR prior LCD assignment.
LCD_RS = 26
LCD_E = 19
LCD_DATA = (13, 6, 5, 11)    # D4, D5, D6, D7 (4-bit mode)
UART_DEVICE = '/dev/serial0'
UART_BAUD = 9600               # VERIFY with CS 350 Thermostat.py template
UART_INTERVAL = 30.0
READ_INTERVAL = 2.0
LCD_ALTERNATE_INTERVAL = 3.0
DEFAULT_SETPOINT_F = 72
BOUNCE_TIME = 0.15

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')


class Mode(Enum):
    OFF = 'off'
    HEAT = 'heat'
    COOL = 'cool'


def active_output(mode: Mode, current_f: float, setpoint_f: int) -> bool:
    """True if this mode requests heat or cooling (no adaptive control)."""
    return ((mode == Mode.HEAT and current_f < setpoint_f) or
            (mode == Mode.COOL and current_f > setpoint_f))


class Thermostat:
    def __init__(self):
        self._lock = Lock()
        self.mode = Mode.OFF
        self.setpoint_f = DEFAULT_SETPOINT_F
        self.current_f = None
        self.running = True
        self.red = PWMLED(RED_LED_PIN, initial_value=0)
        self.blue = PWMLED(BLUE_LED_PIN, initial_value=0)
        self.mode_button = Button(MODE_BUTTON_PIN, pull_up=True, bounce_time=BOUNCE_TIME)
        self.up_button = Button(INCREASE_BUTTON_PIN, pull_up=True, bounce_time=BOUNCE_TIME)
        self.down_button = Button(DECREASE_BUTTON_PIN, pull_up=True, bounce_time=BOUNCE_TIME)
        self.mode_button.when_pressed = self.next_mode
        self.up_button.when_pressed = self.increase_setpoint
        self.down_button.when_pressed = self.decrease_setpoint
        self.i2c = board.I2C()
        self.sensor = adafruit_ahtx0.AHTx0(self.i2c)
        self.lcd = CharLCD(pin_rs=LCD_RS, pin_e=LCD_E, pins_data=LCD_DATA,
                           numbering_mode=GPIO.BCM, cols=16, rows=2)
        self.uart = serial.Serial(port=UART_DEVICE, baudrate=UART_BAUD,
                                  bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                                  stopbits=serial.STOPBITS_ONE, timeout=1)
        self._red_pattern = None
        self._blue_pattern = None
        self._last_lcd = ('', '')

    def next_mode(self):
        with self._lock:
            self.mode = {Mode.OFF: Mode.HEAT, Mode.HEAT: Mode.COOL,
                         Mode.COOL: Mode.OFF}[self.mode]
            logging.info('Changed mode to %s', self.mode.value)

    def increase_setpoint(self):
        with self._lock:
            self.setpoint_f += 1
            logging.info('Setpoint: %d F', self.setpoint_f)

    def decrease_setpoint(self):
        with self._lock:
            self.setpoint_f -= 1
            logging.info('Setpoint: %d F', self.setpoint_f)

    @staticmethod
    def set_led_pattern(led, previous_pattern, pattern):
        """Don't restart fading every loop; preserve continuous PWM animation."""
        if previous_pattern == pattern:
            return previous_pattern
        led.off()
        if pattern == 'fade':
            led.pulse(fade_in_time=1.0, fade_out_time=1.0, n=None, background=True)
        elif pattern == 'solid':
            led.on()
        return pattern

    def update_leds(self, mode, current_f, setpoint_f):
        if current_f is None:   # Sensor error: do not assert heat/cooling request.
            red_pattern = blue_pattern = 'off'
        elif mode == Mode.HEAT:
            red_pattern = 'fade' if active_output(mode, current_f, setpoint_f) else 'solid'
            blue_pattern = 'off'
        elif mode == Mode.COOL:
            blue_pattern = 'fade' if active_output(mode, current_f, setpoint_f) else 'solid'
            red_pattern = 'off'
        else:
            red_pattern = blue_pattern = 'off'
        self._red_pattern = self.set_led_pattern(self.red, self._red_pattern, red_pattern)
        self._blue_pattern = self.set_led_pattern(self.blue, self._blue_pattern, blue_pattern)

    def show_lcd(self, mode, current_f, setpoint_f, alternate):
        first_line = datetime.now().strftime('%m/%d %H:%M:%S')[:16]
        if alternate:
            second_line = f'{mode.value.upper():<4} Set:{setpoint_f:>3}F'
        else:
            second_line = ('Temp: Sensor Err' if current_f is None
                           else f'Temp: {current_f:5.1f}F')
        lines = (first_line.ljust(16), second_line[:16].ljust(16))
        if lines != self._last_lcd:
            for row, content in enumerate(lines):
                if content != self._last_lcd[row]:
                    self.lcd.cursor_pos = (row, 0)
                    self.lcd.write_string(content)
            self._last_lcd = lines

    def send_uart(self, mode, current_f, setpoint_f):
        # Exactly three comma-separated fields; 'nan' flags unavailable sensor.
        value = 'nan' if current_f is None else f'{current_f:.1f}'
        line = f'{mode.value},{value},{setpoint_f}\n'
        self.uart.write(line.encode('ascii'))
        self.uart.flush()
        logging.info('UART: %s', line.strip())

    def run(self):
        next_read = 0.0
        next_uart = time.monotonic() + UART_INTERVAL
        start_time = time.monotonic()
        while self.running:
            now = time.monotonic()
            if now >= next_read:
                try:
                    celsius = float(self.sensor.temperature)
                    # AHT20 returns Celsius; thermostat setpoint is Fahrenheit.
                    reading = celsius * 9.0 / 5.0 + 32.0
                except (OSError, RuntimeError, ValueError) as error:
                    logging.warning('AHT20 read failed: %s', error)
                    reading = None
                with self._lock:
                    self.current_f = reading
                next_read = now + READ_INTERVAL
            with self._lock:
                mode, current, target = self.mode, self.current_f, self.setpoint_f
            self.update_leds(mode, current, target)
            alternate = (int((now - start_time) / LCD_ALTERNATE_INTERVAL) % 2 == 1)
            self.show_lcd(mode, current, target, alternate)
            if now >= next_uart:
                try:
                    self.send_uart(mode, current, target)
                except serial.SerialException as error:
                    logging.error('UART output failed: %s', error)
                next_uart = now + UART_INTERVAL
            time.sleep(0.10)

    def close(self):
        self.running = False
        for resource in ('mode_button', 'up_button', 'down_button',
                         'red', 'blue', 'lcd', 'uart'):
            obj = getattr(self, resource, None)
            if obj is not None:
                try:
                    if resource == 'lcd':
                        obj.clear()
                    obj.close()
                except Exception as error:
                    logging.warning('Shutdown issue with %s: %s', resource, error)
        if getattr(self, 'i2c', None) is not None:
            try:
                self.i2c.deinit()
            except (AttributeError, RuntimeError):
                pass


def main():
    thermostat = None
    try:
        thermostat = Thermostat()
        logging.info('Thermostat started: OFF, setpoint 72 F')
        thermostat.run()
    except KeyboardInterrupt:
        logging.info('User requested exit')
    finally:
        if thermostat is not None:
            thermostat.close()


if __name__ == '__main__':
    main()
