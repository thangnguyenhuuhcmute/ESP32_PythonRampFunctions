import esp32
from machine import Pin
import time
import _thread
import network
import socket
import json
import math

#OUTPUT

SOLENOID_PIN = 36   #15
EN_PIN = 21     #16
STEP_PIN = 47   #17
DIR_PIN = 45    #18

#INPUT 

MOTOR_ALARM_PIN = 35   #12
E_STOP_PIN = 1
HOME_BTN_PIN = 2
CONFIRM02_PIN = 42
CONFIRM01_PIN = 41
HOME_SENSOR_PIN = 40

# CYLINDER_UP_PIN = 38
CYLINDER_DOWN_PIN = 39


STEPS_PER_MM = 1600 / 24   # adjust to your mechanics ≈ 66.67
MAX_POS_MM = 780
MIN_POS_MM = 0

PULSE_US = 10
PERIOD_US = 100   # 10 kHz
DEFAULT_SPEED_MM_S = 75

# -----------------------------
# MOTION PROFILE (RAMP) CONFIG
# -----------------------------
START_SPEED_MM_S = 8       # mm/s  - ramp-up/down start at this speed (always keep > 0)
ACCEL_MM_S2      = 1000    # mm/s^2 - acceleration, increase to faster and decrease to slower
RAMP_SEGMENTS    = 24      # the number of speed on each ramp range (higher to smoother)


# -----------------------------
# PINS
# -----------------------------

#OUT

dir_pin = Pin(DIR_PIN, Pin.OUT,value=1)
en_pin = Pin(EN_PIN, Pin.OUT, value=1)   # enable driver
solenoid = Pin(SOLENOID_PIN, Pin.OUT, value=0)


#IN

home = Pin(HOME_SENSOR_PIN, Pin.IN, Pin.PULL_UP)
home_button = Pin(HOME_BTN_PIN, Pin.IN, Pin.PULL_UP)
motor_alarm = Pin(MOTOR_ALARM_PIN, Pin.IN, Pin.PULL_UP)
e_stop = Pin(E_STOP_PIN, Pin.IN, Pin.PULL_UP)
confirm_01 = Pin(CONFIRM01_PIN, Pin.IN, Pin.PULL_UP)
confirm_02 = Pin(CONFIRM02_PIN, Pin.IN, Pin.PULL_UP)


# cylinder_up = Pin(CYLINDER_UP_PIN, Pin.IN, Pin.PULL_UP)
cylinder_down = Pin(CYLINDER_DOWN_PIN, Pin.IN, Pin.PULL_UP)


# -----------------------------
# RMT (single global instance)
# -----------------------------
rmt = esp32.RMT(pin=Pin(STEP_PIN), resolution_hz=1_000_000)


# -----------------------------
# STATE
# -----------------------------
position_steps = 0
positive_dir = 0
negative_dir = 1



# ----------------------------
# MACHINE STATE VARIABLES
# ----------------------------

current_pos_mm = 0
current_pos = 1
solenoid_done = False

# Default values

POS1 = 0
POS2 = 150
POS3 = 300
POS4 = 450
POS5 = 700
POS6 = 600

# ----------------------------
# CONFIG FILE
# ----------------------------

CONFIG_FILE = "config.json"


def load_config():
    global POS1, POS2, POS3, POS4, POS5, POS6

    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)

        POS1 = data["pos1"]
        POS2 = data["pos2"]
        POS3 = data["pos3"]
        POS4 = data["pos4"]
        POS5 = data["pos5"]
        POS6 = data["pos6"]

        print("Config loaded")

    except:
        print("No config file, using defaults")


def save_config():

    data = {
    "pos1": round(POS1, 2),
    "pos2": round(POS2, 2),
    "pos3": round(POS3, 2),
    "pos4": round(POS4, 2),
    "pos5": round(POS5, 2),
    "pos6": round(POS6, 2)
    }

    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

    print("Config saved")

def get_status_json():

    status = {
        "pos_mm": round(get_pos_mm(), 2),
        "state": "P" + str(current_pos),
        "alarm": motor_alarm.value(),
        "estop": e_stop.value(),
        "solenoid": solenoid.value(),
        "cylinder_down": cylinder_down.value()

    }

    return json.dumps(status)
# ----------------------------
# HTML PAGE LOADER
# ----------------------------

def load_html():

    with open("index.html") as f:
        html = f.read()

    html = html.replace("%POS1%", "{:.2f}".format(POS1))
    html = html.replace("%POS2%", "{:.2f}".format(POS2))
    html = html.replace("%POS3%", "{:.2f}".format(POS3))
    html = html.replace("%POS4%", "{:.2f}".format(POS4))
    html = html.replace("%POS5%", "{:.2f}".format(POS5))
    html = html.replace("%POS6%", "{:.2f}".format(POS6))

    html = html.replace("%CURPOS%", str(round(get_pos_mm(),2)))
    html = html.replace("%STATE%", "P" + str(current_pos))

    return html

# ----------------------------
# WIFI SETUP
# ----------------------------

def start_ap():

    ap = network.WLAN(network.WLAN.IF_AP)
    ap.active(True)

    ap.config(
        essid="BAC88_MACHINE",
        password="12345678",
        authmode=network.AUTH_WPA_WPA2_PSK,
        max_clients=5
    )

    print("Access Point Started")
    print("Connect to WiFi: ESP32_MACHINE")
    print("Password: 12345678")
    print("IP:", ap.ifconfig()[0])
# ----------------------------
# PARSE HTTP PARAMETERS
# ----------------------------

def parse_params(request):

    params = {}

    try:
        query = request.split(" ")[1]

        if "?" in query:
            query = query.split("?")[1]

            pairs = query.split("&")

            for p in pairs:
                k, v = p.split("=")
                params[k] = v

    except:
        pass

    return params


# ----------------------------
# WEB SERVER
# ----------------------------

def handle_machine_command(request):
    global POS1, POS2, POS3, POS4, POS5, POS6
    # Safety check first
    if safety_check():
        return
       # ---------------- SAVE POSITIONS ----------------
    if "GET /save?" in request:

        params = parse_params(request)

        try:
            POS1 = round(float(params.get("pos1", POS1)),2)
            POS2 = round(float(params.get("pos2", POS2)),2)
            POS3 = round(float(params.get("pos3", POS3)),2)
            POS4 = round(float(params.get("pos4", POS4)),2)
            POS5 = round(float(params.get("pos5", POS5)),2)
            POS6 = round(float(params.get("pos6", POS6)),2)

            save_config()

            print("Saved:", POS1, POS2, POS3, POS4, POS5,POS6)

        except Exception as e:
            print("Save error:", e)

        return

    # ---------------- HOME ----------------
    if "GET /home" in request:
        print("WEB: HOME")
        _thread.start_new_thread(home_axis, ())


    # ---------------- JOG + ----------------
    elif "GET /jog?dir=+" in request:
        print("WEB: JOG +")
        jog_start(positive_dir)


    # ---------------- JOG - ----------------
    elif "GET /jog?dir=-" in request:
        print("WEB: JOG -")
        jog_start(negative_dir)


    # ---------------- STOP ----------------
    elif "GET /stop" in request:
        print("WEB: STOP")
        jog_stop()


    # ---------------- SOLENOID TEST ----------------
    elif "GET /solenoid" in request:
        print("WEB: SOLENOID TEST")
        _thread.start_new_thread(activate_solenoid, ())


    # ---------------- GO TO POSITION ----------------
    elif "GET /go?mm=" in request:

        try:

            part = request.split("mm=")[1]

            mm = part.split("&")[0]
            mm = round(float(mm),2)

            speed = DEFAULT_SPEED_MM_S

            if "speed=" in request:
                speed = request.split("speed=")[1].split(" ")[0]
                speed = float(speed)

            print("WEB MOVE:", mm, "mm  speed:", speed)

            _thread.start_new_thread(move_to_mm,(mm, speed))

        except:
            print("Invalid GO command")

def start_server():

    while True:

        try:

            addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]

            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            s.bind(addr)
            s.listen(3)

            print("Web server running")

            while True:

                try:
                    cl, addr = s.accept()
                except OSError as e:
                    print("Accept error:", e)
                    time.sleep_ms(100)
                    continue

                try:

                    request = cl.recv(1024)

                    if not request:
                        continue

                    request = request.decode()

                    # STATUS API
                    if "GET /status" in request:

                        response = get_status_json()

                        cl.send("HTTP/1.1 200 OK\r\n")
                        cl.send("Content-Type: application/json\r\n")
                        cl.send("Connection: close\r\n\r\n")
                        cl.send(response)

                    else:

                        handle_machine_command(request)

                        html = load_html()

                        cl.send("HTTP/1.1 200 OK\r\n")
                        cl.send("Content-Type: text/html\r\n")
                        cl.send("Connection: close\r\n\r\n")
                        cl.send(html)

                except Exception as e:
                    print("Client error:", e)

                finally:
                    try:
                        cl.close()
                    except:
                        pass

        except Exception as e:

            print("SERVER CRASH:", e)

            try:
                s.close()
            except:
                pass

            time.sleep(2)

# -----------------------------
# MOTION
# -----------------------------

def is_cylinder_down():
    return cylinder_down.value() == 0   # active LOW




def speed_to_period_us(speed_mm_s):

    steps_per_sec = speed_mm_s * STEPS_PER_MM

    if steps_per_sec <= 0:
        steps_per_sec = 1

    period_us = int(1_000_000 / steps_per_sec)

    if period_us < (PULSE_US + 2):
        period_us = PULSE_US + 2

    return period_us

def safety_check():

    if motor_alarm.value() == 0:   # active LOW
        jog_stop()
        print("Motor Alarm!")
        return True

    if e_stop.value() == 1:        # active HIGH
        jog_stop()
        print("E-STOP!")
        return True

    return False

def motor_reset():
    # safety_check()
    en_pin.value(0)
    time.sleep_ms(500)
    en_pin.value(1)
    time.sleep_ms(1500)
def _pulse_block(steps, speed_mm_s):
    # Generate steps pulses at constant speed.
    # Direction need to set before call the definition.
    if steps <= 0:
        return

    period_us = speed_to_period_us(speed_mm_s)

    rmt.loop_count(steps)
    rmt.write_pulses((PULSE_US, period_us - PULSE_US), 1)

    move_time_ms = int((steps * period_us) / 1000)
    time.sleep_ms(move_time_ms)

    rmt.active(False)


def _ramp_block(total_steps, v_from, v_to, segments):
    # Generate the 'total_steps' while step scan: v_from -> v_to,
    # Segments the small range with constant speed.
    # Use v^2 liner with distance => constant accerleration (trapezoidal).
    if total_steps <= 0:
        return

    segs = segments if segments < total_steps else total_steps
    if segs < 1:
        segs = 1

    base  = total_steps // segs
    extra = total_steps - base * segs

    v_from2 = v_from * v_from
    v_to2   = v_to * v_to

    for i in range(segs):
        frac = (i + 0.5) / segs
        v = math.sqrt(v_from2 + (v_to2 - v_from2) * frac)

        seg_steps = base + (1 if i < extra else 0)
        _pulse_block(seg_steps, v)


def move_steps(steps, direction, speed_mm_s=DEFAULT_SPEED_MM_S):

    global position_steps

    if steps <= 0:
        print("No steps to move")
        return

    v_start  = START_SPEED_MM_S
    v_cruise = speed_mm_s if speed_mm_s > v_start else v_start
    accel    = ACCEL_MM_S2

    # set chieu 1 lan cho ca hanh trinh
    dir_pin.value(not direction)
    time.sleep_us(20)

    # The number of step need to speedup v_start -> v_cruise (and speed down)
    accel_dist_mm = (v_cruise * v_cruise - v_start * v_start) / (2.0 * accel)
    accel_steps   = int(accel_dist_mm * STEPS_PER_MM)

    # Short range: v_cruise -> triangle profile
    if accel_steps * 2 > steps:
        accel_steps = steps // 2

    decel_steps  = accel_steps
    cruise_steps = steps - accel_steps - decel_steps

    print(f"Move {steps} steps | cruise={v_cruise} mm/s | ramp={accel_steps} steps")

    # ACCEL  : v_start -> v_cruise
    _ramp_block(accel_steps, v_start, v_cruise, RAMP_SEGMENTS)

    # CRUISE : giu v_cruise
    _pulse_block(cruise_steps, v_cruise)

    # DECEL  : v_cruise -> v_start (soft stop)
    _ramp_block(decel_steps, v_cruise, v_start, RAMP_SEGMENTS)

    if direction == 1:
        position_steps += steps
    else:
        position_steps -= steps

    print(f"New pos: {get_pos_mm():.2f} mm")

def jog_start(direction, speed_mm_s=DEFAULT_SPEED_MM_S):

    if safety_check():
        return

    jog_stop()

    period_us = speed_to_period_us(speed_mm_s)

    dir_pin.value(direction)
    time.sleep_us(20)

    rmt.loop_count(-1)
    rmt.write_pulses((PULSE_US, period_us - PULSE_US), 1)

    print(f"Jogging {'+' if direction==positive_dir else '-'} | speed={speed_mm_s} mm/s")

    time.sleep_ms(200)

    safety_check()

def jog_stop():
    rmt.active(False)
    print("Jog stopped")

def activate_solenoid():

    print("Solenoid ON")
    solenoid.value(1)

    start = time.ticks_ms()
    detected = False

    #  WAIT UP TO 5s FOR CYLINDER DOWN
    while time.ticks_diff(time.ticks_ms(), start) < 5000:

        if is_cylinder_down():
            print("Cylinder DOWN detected")
            detected = True
            break

        time.sleep_ms(10)

    if detected:
        #  WAIT EXTRA 5s
        print("Holding 5s...")
        time.sleep(5)

    else:
        #  ERROR
        print("ERROR: Cylinder did NOT go down!")

    solenoid.value(0)
    print("Solenoid OFF")
# -----------------------------
# POSITION
# -----------------------------
def get_pos_mm():
    return position_steps / STEPS_PER_MM

# -----------------------------
# HOMING
# -----------------------------
def home_axis():
    global position_steps
    print("Homing...")
    if safety_check():
        motor_reset()

    if home.value() == 0:
        jog_start(positive_dir)   # positive direction
    while home.value() == 0:   # still pressing switch
        pass
    jog_stop()

    time.sleep_ms(200)

    # 1️ Move toward HOME
    jog_start(negative_dir,30)   # negative direction
    while home.value() == 1:   # switch not triggered
        pass
    jog_stop()

    time.sleep_ms(200)

    # 2️ Back off until switch releases
    jog_start(positive_dir,20)   # positive direction
    while home.value() == 0:   # still pressing switch
        pass
    jog_stop()

    time.sleep_ms(200)

    # 3 Move toward HOME
    jog_start(negative_dir,10)   # negative direction
    while home.value() == 1:   # switch not triggered
        pass
    jog_stop()

    time.sleep_ms(200)

    position_steps = 0
    print("Homed at 0mm")

# -----------------------------
# MOVE TO MM
# -----------------------------
def move_to_mm(target_mm, speed_mm_s=DEFAULT_SPEED_MM_S):

    global position_steps

    # SAFETY: block movement if cylinder not safe
    if solenoid.value() == 1 and not is_cylinder_down():
        print("BLOCKED: Cylinder not down while solenoid active")
        return

    if target_mm < MIN_POS_MM or target_mm > MAX_POS_MM:
        print("Soft limit block")
        return

    target_steps = int(target_mm * STEPS_PER_MM)
    delta = target_steps - position_steps

    print(f"Move request → {target_mm} mm")

    if delta == 0:
        print("Already at position")
        return

    direction = 1 if delta > 0 else 0

    move_steps(abs(delta), direction, speed_mm_s)

def check_home_button():

    if home_button.value() == 0:
        print("Home button pressed")
        home_axis()

        global current_pos
        current_pos = 1

        while home_button.value() == 0:
            time.sleep_ms(10)

def wait_release():
    while confirm_01.value() == 0 or confirm_02.value() == 0:
        time.sleep_ms(10)

def state_machine():

    global current_pos
    global solenoid_done

    c1 = confirm_01.value() == 0
    c2 = confirm_02.value() == 0

    # -------- P1 --------
    if current_pos == 1:
        if c1:
            move_to_mm(POS2)
            current_pos = 2
            wait_release()

    # -------- P2 --------
    elif current_pos == 2:
        if c1:
            move_to_mm(POS3)
            current_pos = 3
            solenoid_done = False
            wait_release()

    # -------- P3 --------
    elif current_pos == 3:

        # dual confirm activates solenoid
        if c1 and c2 and not solenoid_done:
            activate_solenoid()
            solenoid_done = True
            wait_release()

        # move to P4 only after solenoid cycle finished
        elif c1 and solenoid_done:
            move_to_mm(POS4)
            current_pos = 4
            wait_release()

    # -------- P4 --------
    elif current_pos == 4:
        if c1:
            move_to_mm(POS5)
            current_pos = 5
            wait_release()

    # -------- P5 --------
    elif current_pos == 5:
        if c1:
            move_to_mm(POS6)
            current_pos = 6
            wait_release()

    # -------- P6 --------
    elif current_pos == 6:
        if c1:
            move_to_mm(POS1)
            current_pos = 1
            wait_release()

def print_inputs_status():
    print("------ INPUT STATUS ------")
    print("HOME SENSOR   :", home.value())
    print("HOME BUTTON   :", home_button.value())
    print("MOTOR ALARM   :", motor_alarm.value())
    print("E-STOP        :", e_stop.value())
    print("CONFIRM 01    :", confirm_01.value())
    print("CONFIRM 02    :", confirm_02.value())
    print("CYLINDER UP   :", cylinder_up.value())
    print("CYLINDER DOWN :", cylinder_down.value())
    print("-----output------------\n")
    print("Dir :", dir_pin.value())
    print("EN :", en_pin.value())
    print("Solenoid :", solenoid.value())
    print("--------monitor---------\n")
    print("POS        :", current_pos)
    print("POSITION     :", round(get_pos_mm(), 2), "mm")
    print("--------------------------\n")

def dashboard_task():

    while True:
        print_inputs_status()
        time.sleep(1)

# _thread.start_new_thread(dashboard_task, ())
# -----------------------------
# MAIN
# -----------------------------
load_config()
start_ap()
# run web server in another core
_thread.start_new_thread(start_server, ())
home_axis()

while True:

    if safety_check():
        continue

    check_home_button()

    state_machine()

    time.sleep_ms(10)