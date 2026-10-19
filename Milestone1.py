# CS 350 Milestone One - PWM Lab
# Author: Von Ray Giron
# Description: Uses pulse width modulation (PWM) on GPIO 18 to fade an LED
# from off to full brightness and back to off until the user presses Ctrl+C.

import time
import RPi.GPIO as GPIO

# Use Broadcom (BCM) GPIO numbering.
GPIO.setmode(GPIO.BCM)

# Configure GPIO 18 as an output for the LED.
GPIO.setup(18, GPIO.OUT)

# Create a PWM instance on GPIO 18 at 60 Hz.
pwm18 = GPIO.PWM(18, 60)

# Start the PWM instance with a 0% duty cycle (LED off).
pwm18.start(0)

try:
    while True:
        # Fade the LED in from 0% to 100% duty cycle.
        for dutyCycle in range(0, 105, 5):
            pwm18.ChangeDutyCycle(dutyCycle)
            time.sleep(0.1)

        # Fade the LED out from 100% to 0% duty cycle.
        for dutyCycle in range(100, -5, -5):
            pwm18.ChangeDutyCycle(dutyCycle)
            time.sleep(0.1)

except KeyboardInterrupt:
    print("Stopping PWM and cleaning up GPIO.")

finally:
    pwm18.stop()
    GPIO.cleanup()
