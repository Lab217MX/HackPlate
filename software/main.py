# Hotplate – MicroPython (RPi Pico)
# Menu textual (sin iconos), encoder con filtro, NTC pull-up, SSR con histéresis,
# OLED 1.3" (SSD1306 o SH1106), self-test, LED estado, debug.

from machine import Pin, I2C, ADC
import utime, math

# ===================== DISPLAY =====================
DISPLAY_CONTROLLER = "SH1106"   # "SSD1306" o "SH1106"
OLED_ADDR = 0x3C
I2C_SDA, I2C_SCL = 4, 5
SCREEN_W, SCREEN_H = 128, 64

i2c = I2C(0, sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=400_000)
if DISPLAY_CONTROLLER.upper() == "SSD1306":
    from ssd1306 import SSD1306_I2C
    oled = SSD1306_I2C(SCREEN_W, SCREEN_H, i2c, addr=OLED_ADDR)
elif DISPLAY_CONTROLLER.upper() == "SH1106":
    from sh1106 import SH1106_I2C
    oled = SH1106_I2C(SCREEN_W, SCREEN_H, i2c, addr=OLED_ADDR)
    oled.flip()
else:
    raise ValueError("DISPLAY_CONTROLLER debe ser 'SSD1306' o 'SH1106'")

# ===================== PINES =====================
CON = 6      # OK
PSH = 7      # Push encoder
BAK = 8      # Back
TRA = 2      # Encoder A
TRB = 3      # Encoder B
SSR_PIN   = 15
NTC_PIN   = 26
LED_STATUS = 25

# ===================== BOTONES =====================
class Button:
    def __init__(self, pin, debounce=30):
        self.pin = Pin(pin, Pin.IN, Pin.PULL_UP)
        self.debounce = debounce
        self._state = 1
        self._stable = 1
        self._last = utime.ticks_ms()
    def pressed_edge(self):
        raw = self.pin.value()
        now = utime.ticks_ms()
        if raw != self._state:
            self._state = raw
            self._last = now
        if utime.ticks_diff(now, self._last) > self.debounce and self._stable != self._state:
            self._stable = self._state
            if self._stable == 0:
                return True
        return False
    def held(self): return self.pin.value()==0

btnOK, btnPSH, btnBAK = Button(CON), Button(PSH), Button(BAK)

# ===================== ENCODER (IRQ + filtro por detente) =====================
_enc_lookup = [0,-1,+1,0, +1,0,0,-1, -1,0,0,+1, 0,+1,-1,0]
class RotaryEncoder:
    def __init__(self, pin_a, pin_b):
        self._a = Pin(pin_a, Pin.IN, Pin.PULL_UP)
        self._b = Pin(pin_b, Pin.IN, Pin.PULL_UP)
        self._pos = 0
        self._state = (self._a.value()<<1)|self._b.value()
        self._a.irq(self._irq, Pin.IRQ_RISING|Pin.IRQ_FALLING)
        self._b.irq(self._irq, Pin.IRQ_RISING|Pin.IRQ_FALLING)
    def _irq(self, p):
        s = (self._a.value()<<1)|self._b.value()
        self._pos += _enc_lookup[(self._state<<2)|s]
        self._state = s
    def get(self): return self._pos
    def reset(self, v=0): self._pos = v

enc = RotaryEncoder(TRA, TRB)
enc_last = 0
enc_accum = 0
DETENT_THRESHOLD = 4

def encoder_steps():
    """Devuelve -1/0/+1 por 'clic' usando filtro por detente."""
    global enc_last, enc_accum
    pos = enc.get()
    delta = pos - enc_last
    if delta:
        enc_last = pos
        enc_accum += delta
        if enc_accum >= DETENT_THRESHOLD:
            enc_accum -= DETENT_THRESHOLD
            print("[ENC] +step")
            return +1
        elif enc_accum <= -DETENT_THRESHOLD:
            enc_accum += DETENT_THRESHOLD
            print("[ENC] -step")
            return -1
    return 0

# ===================== LED STATUS =====================
# 0=OFF, 1=ON, 2=blink lento, 3=blink rápido, 4=doble blink error
led = Pin(LED_STATUS, Pin.OUT)
_led_mode, _led_state, _led_last, _err_step = 0, 0, utime.ticks_ms(), 0
def set_led_mode(mode:int):
    global _led_mode
    if _led_mode!=mode: print("[LED] ->", mode)
    _led_mode = mode
    if mode==0: led.value(0)
    if mode==1: led.value(1)
def update_led(now):
    global _led_state,_led_last,_err_step
    if _led_mode==0: led.value(0)
    elif _led_mode==1: led.value(1)
    elif _led_mode==2:
        if utime.ticks_diff(now,_led_last)>500:
            _led_last=now; _led_state^=1; led.value(_led_state)
    elif _led_mode==3:
        if utime.ticks_diff(now,_led_last)>100:
            _led_last=now; _led_state^=1; led.value(_led_state)
    elif _led_mode==4:
        if utime.ticks_diff(now,_led_last)>200:
            _led_last=now; _err_step+=1
            if _err_step in (1,2): _led_state^=1
            elif _err_step<=5: _led_state=0
            else: _err_step=0; _led_state=0
            led.value(_led_state)

# ===================== NTC / ADC (pull-up configurable) =====================
adc = ADC(NTC_PIN)
NTC_PULLUP = True       # True = R fija a 3V3, NTC a GND
VREF, ADC_MAX = 3.3, 65535.0
R_FIXED = 10_000.0
NTC_R0, NTC_T0, NTC_B = 100_000.0, 298.15, 3950.0

def _avg_adc_u16(adc_obj, n=8):
    s=0
    for _ in range(n): s+=adc_obj.read_u16()
    return s/n

def read_temp_c():
    raw = _avg_adc_u16(adc,8)
    v = raw*VREF/ADC_MAX
    print("[ADC] raw_avg=",int(raw)," V=",round(v,4))
    if v<0.02 or v>(VREF-0.02):
        print("[NTC] fuera de rango"); return None
    if NTC_PULLUP:
        r_ntc = (v*R_FIXED)/(VREF-v)
    else:
        r_ntc = R_FIXED*(VREF/v - 1.0)
    invT = (1.0/NTC_T0) + (1.0/NTC_B)*math.log(r_ntc/NTC_R0)
    T = (1.0/invT) - 273.15
    print("[NTC] R=",round(r_ntc,1)," T=",round(T,2))
    return T

# ===================== CONTROL =====================
ssr = Pin(SSR_PIN, Pin.OUT); ssr.value(0)
setpointC, tempC, HYST, heating = 120.0, 25.0, 3.0, False

# ===================== UI HELPERS =====================
def draw_header(title):
    oled.text(title, 0, 0)
    oled.hline(0,10,SCREEN_W,1)

def draw_bar(value, minV, maxV, x=0, y=54, w=128, h=8):
    if maxV<=minV: maxV=minV+1.0
    v = max(min(value,maxV),minV)
    p = (v-minV)/(maxV-minV)
    fill = int(p*(w-2))
    oled.rect(x,y,w,h,1)
    if fill>0: oled.fill_rect(x+1,y+1,fill,h-2,1)

# Flamita 16x16 (a partir de 8x8 escalada x2)
_flame1 = [0x18,0x3C,0x7E,0x6E,0x3C,0x18,0x18,0x00]
_flame2 = [0x18,0x3C,0x6E,0x7E,0x3C,0x18,0x18,0x00]
def draw_bitmap8_scaled(x,y,bmp,scale=2):
    for r in range(8):
        row = bmp[r]
        for c in range(8):
            if row & (0x80>>c):
                for i in range(scale):
                    for j in range(scale):
                        oled.pixel(x+c*scale+i, y+r*scale+j, 1)
def draw_flame(x,y,frame,scale=1):
    draw_bitmap8_scaled(x,y, _flame1 if frame else _flame2, scale)

# ===================== MODOS & MENÚ (solo texto) =====================
MODE_MENU, MODE_EDIT, MODE_HEAT, MODE_TEST = 0,1,2,3
mode = MODE_MENU
menu_sel = 0
flame_frame = False

def enter_edit():
    global mode
    mode = MODE_EDIT
    set_led_mode(3)

def enter_heat():
    global mode
    mode = MODE_HEAT
    set_led_mode(1)

def enter_test():
    global mode, test_state, test_step, test_msgs, got_encoder_move
    mode = MODE_TEST
    test_state = 1; test_step=0; test_msgs=[]; got_encoder_move=False
    print("[TEST] start"); set_led_mode(3)

MENU_ITEMS = [
    ("Editar", enter_edit),
    ("Empezar", enter_heat),
    ("Selftest", enter_test),
]
# Para añadir más: MENU_ITEMS.append(("Nueva opcion", handler_func))

def render_menu():
    oled.fill(0); draw_header("Menu Principal")
    top_y = 14
    for i,(label,_) in enumerate(MENU_ITEMS):
        y = top_y + i*16
        # resaltado de la opción seleccionada (barra invertida)
        if i==menu_sel:
            oled.fill_rect(2,y-2,SCREEN_W-4,16,1)
            oled.text(label, 0, y+2, 0)
        else:
            oled.text(label, 0, y+2, 1)
    # barra estética lateral
    oled.fill_rect(SCREEN_W-4, top_y-2, 2, SCREEN_H-(top_y-2), 1)
    oled.show()

# ===================== SELF-TEST =====================
test_state=0; test_step=0; test_msgs=[]; test_wait_until=0; got_encoder_move=False
def test_draw():
    oled.fill(0); draw_header("Self-Check")
    y=14
    for m in test_msgs[-4:]:
        oled.text(m,0,y); y+=12
    oled.text("OK=Sig  BAK=Salir",0,54); oled.show()

def run_test_step():
    global test_step,test_msgs,test_wait_until,got_encoder_move
    now=utime.ticks_ms()
    if test_step==0:
        test_msgs.append("OLED: OK")
        test_msgs.append("LED: Blink rapido")
        test_wait_until=now+600; test_step=1
    elif test_step==1:
        if utime.ticks_diff(now,test_wait_until)>=0:
            t=read_temp_c()
            test_msgs.append("NTC: {}".format("FAIL" if (t is None or t<-20 or t>400) else "OK {:.1f}C".format(t)))
            test_step=2
    elif test_step==2:
        test_msgs.append("Gira el encoder...")
        test_wait_until=now+2000; got_encoder_move=False; test_step=3
    elif test_step==3:
        if utime.ticks_diff(now,test_wait_until)>=0:
            test_msgs.append("Encoder: {}".format("OK" if got_encoder_move else "FAIL"))
            test_step=4
    elif test_step==4:
        test_msgs.append("Pulsa PSH..."); test_step=5
    elif test_step==5:
        if btnPSH.pressed_edge(): test_msgs.append("PSH: OK"); test_step=6
    elif test_step==6:
        test_msgs.append("Pulsa CON..."); test_step=7
    elif test_step==7:
        if btnOK.pressed_edge(): test_msgs.append("CON: OK"); test_step=8
    elif test_step==8:
        test_msgs.append("Pulsa BAK..."); test_step=9
    elif test_step==9:
        if btnBAK.pressed_edge(): test_msgs.append("BAK: OK"); test_step=10
    elif test_step==10:
        test_msgs.append("SSR: pulso 200ms")
        ssr.value(1); utime.sleep_ms(200); ssr.value(0)
        test_msgs.append("Self-Check: DONE")
        set_led_mode(1)
        return True
    return False

# ===================== SETUP =====================
print("[INFO] Boot. I2C scan:", i2c.scan())
set_led_mode(2)  # arrancando
oled.fill(0); oled.text("Glitchboi", 4, 24); oled.show()
utime.sleep_ms(700)
mode = MODE_MENU
set_led_mode(1)
render_menu()

# ===================== LOOP =====================
t_sample = utime.ticks_ms()
t_ui     = t_sample
t_flame  = t_sample

while True:
    now = utime.ticks_ms()

    # ----- ENCODER (con filtro) -----
    step = encoder_steps()
    if step:
        if mode == MODE_MENU:
            menu_sel = (menu_sel + step) % len(MENU_ITEMS)
            render_menu()
        elif mode == MODE_EDIT:
            setpointC = min(max(setpointC + step*5.0, 50.0), 260.0)
            print("[SET] setpoint=", setpointC)
        elif mode == MODE_TEST:
            got_encoder_move = True

    # ----- BOTONES -----
    if btnPSH.pressed_edge():
        print("[BTN] PSH")
        if mode == MODE_MENU:
            _, handler = MENU_ITEMS[menu_sel]
            handler()
    if btnOK.pressed_edge():
        print("[BTN] OK")
        if mode == MODE_MENU:
            _, handler = MENU_ITEMS[menu_sel]
            handler()
        elif mode == MODE_EDIT:
            mode = MODE_MENU; set_led_mode(1); render_menu()
    if btnBAK.pressed_edge():
        print("[BTN] BAK")
        if mode in (MODE_EDIT, MODE_HEAT, MODE_TEST):
            mode = MODE_MENU; set_led_mode(1); render_menu()

    # ----- SELF-TEST -----
    if mode == MODE_TEST:
        done = run_test_step()
        test_draw()
        if done:
            utime.sleep_ms(900)
            mode = MODE_MENU; render_menu()

    # ----- CONTROL TEMP (solo HEAT) -----
    if utime.ticks_diff(now, t_sample) >= 100:
        t_sample = now
        t = read_temp_c()
        if t is None or t<-20 or t>400:
            if heating: print("[SSR] OFF por error NTC")
            ssr.value(0); heating=False; set_led_mode(4)
        else:
            if _led_mode==4: set_led_mode(1 if mode!=MODE_EDIT else 3)
            tempC = t
            if mode == MODE_HEAT:
                if tempC < setpointC - HYST:
                    if not heating: print("[SSR] ON")
                    ssr.value(1); heating=True
                elif tempC > setpointC + HYST:
                    if heating: print("[SSR] OFF")
                    ssr.value(0); heating=False
            else:
                if heating: print("[SSR] OFF (no heat)")
                ssr.value(0); heating=False

    # ----- UI -----
    if utime.ticks_diff(now, t_ui) >= 33:
        t_ui = now
        if mode == MODE_MENU:
            pass  # ya se pintó
        elif mode == MODE_EDIT:
            oled.fill(0); draw_header("Editar temperatura")
            oled.text("Actual: {:0.1f}C".format(tempC), 0, 18)
            oled.text("Set:    {:0.1f}C".format(setpointC), 0, 34)
            oled.hline(0, 46, 100, 1)
            oled.text("Gira | OK=Guardar | BAK", 0, 54)
            oled.show()
        elif mode == MODE_HEAT:
            oled.fill(0); draw_header("Calentando")
            oled.text("T: {:0.1f}C".format(tempC), 0, 18)
            oled.text("Set: {:0.1f}C".format(setpointC), 0, 34)
            if heating:
                if utime.ticks_diff(now,t_flame)>180:
                    t_flame=now; flame_frame=not flame_frame
                draw_flame(100,16,flame_frame,2)  # flama 16x16
            else:
                oled.fill_rect(104,18,6,16,1); oled.fill_rect(114,18,6,16,1)
            barV = tempC if tempC<setpointC else setpointC
            draw_bar(barV, 0.0, max(100.0,setpointC))
            oled.text("BAK=Detener", 0, 54)
            oled.show()
        elif mode == MODE_TEST:
            pass

    # ----- LED -----
    update_led(now)
    utime.sleep_ms(1)
