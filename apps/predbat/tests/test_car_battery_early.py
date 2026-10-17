# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init

"""Tests for charging the car early from the home battery (car_charging_battery_early).

With car_charging_from_battery the battery supplies the car in a planned slot that is not a charge window, so
that charge costs the same taken now - plan_car_battery_early moves a chunk of it forward when scoring shows
it is free, and never into an export or charge window.
"""

import copy

from const import CAR_BATTERY_EARLY_MINUTES, EXPORT_LIMIT_IDLE
from prediction import Prediction
from tests.test_infra import reset_rates, reset_inverter


def setup_battery_early(my_predbat, import_rate=150.0, export_rate=5.0, forecast_hours=24, soc_kw=50.0):
    """Put my_predbat into a state where plan_car_battery_early can run: full battery, flat rates, one car."""
    my_predbat.load_user_config()
    my_predbat.fetch_config_options()
    reset_inverter(my_predbat)

    my_predbat.forecast_minutes = forecast_hours * 60
    my_predbat.end_record = my_predbat.forecast_minutes

    pv_step = {}
    load_step = {}
    for minute in range(0, my_predbat.forecast_minutes + my_predbat.plan_interval_minutes, 5):
        pv_step[minute] = 0.0
        load_step[minute] = 0.05

    my_predbat.load_minutes_step = load_step
    my_predbat.load_minutes_step10 = load_step
    my_predbat.pv_forecast_minute_step = pv_step
    my_predbat.pv_forecast_minute10_step = pv_step
    my_predbat.prediction = Prediction(my_predbat, pv_step, pv_step, load_step, load_step)

    reset_rates(my_predbat, import_rate, export_rate)
    my_predbat.rate_import_base = my_predbat.rate_import.copy()
    my_predbat.rate_export_base = my_predbat.rate_export.copy()

    # A disabled charge window well clear of now and of the car's slots
    my_predbat.charge_limit_best = [0.0]
    my_predbat.charge_window_best = [{"start": my_predbat.minutes_now + 20 * 60, "end": my_predbat.minutes_now + 20 * 60 + 30, "average": import_rate}]
    my_predbat.export_window_best = []
    my_predbat.export_limits_best = []
    my_predbat.isExporting = False

    my_predbat.soc_max = 50.0
    my_predbat.soc_kw = soc_kw
    my_predbat.battery_rate_max_discharge = 5 / 60.0
    my_predbat.battery_rate_max_charge = 5 / 60.0
    # reset_inverter leaves a 1kW inverter, which the car's 4kW cannot come through
    my_predbat.inverter_limit = 10 / 60.0
    my_predbat.export_limit = 10 / 60.0

    my_predbat.num_cars = 1
    my_predbat.car_charging_from_battery = True
    my_predbat.car_charging_battery_early = True
    my_predbat.car_charging_planned = [True]
    my_predbat.car_charging_battery_size = [50.0]
    my_predbat.car_charging_limit = [4.0]
    my_predbat.car_charging_soc = [0.0]
    my_predbat.car_charging_soc_next = [None]
    my_predbat.car_charging_rate = [4.0]
    my_predbat.car_charging_loss = 1.0
    my_predbat.car_charging_plan_max_price = [0]
    my_predbat.car_charging_plan_smart = [True]
    my_predbat.car_charging_now = [False]
    my_predbat.car_charging_now_slots = [[]]
    my_predbat.car_battery_early_active = {}
    my_predbat.car_battery_early_base = {}

    # A ready time twelve hours out, and hourly candidate windows from four hours on
    ready = my_predbat.minutes_now + 12 * 60
    my_predbat.car_charging_plan_time = ["{:02d}:{:02d}:00".format((ready // 60) % 24, ready % 60)]
    my_predbat.low_rates = []
    for hour in range(4, 12):
        start = my_predbat.minutes_now + hour * 60
        my_predbat.low_rates.append({"start": start, "end": start + 60, "average": my_predbat.rate_import.get(start, import_rate)})

    base = my_predbat.plan_car_charging(0, my_predbat.low_rates)
    my_predbat.car_charging_slots = [base]
    my_predbat.car_battery_early_base = {0: base}
    return base


def capture_log(my_predbat, func):
    """Run func with self.log captured, returning the battery early lines it emitted."""
    captured = []
    original = my_predbat.log

    def collect(message):
        """Record the message, then let the real logger have it."""
        captured.append(str(message))
        return original(message)

    my_predbat.log = collect
    try:
        func()
    finally:
        my_predbat.log = original
    return [line for line in captured if "battery early" in line]


def run_slots_arithmetic(my_predbat):
    """A chunk moves the right energy out of the right slot and keeps the slot's charging rate."""
    failed = False
    print("**** Running Test: battery early slot arithmetic ****")

    base = setup_battery_early(my_predbat)
    now = my_predbat.minutes_now
    if len(base) != 1 or abs(base[0]["kwh"] - 4.0) > 0.001:
        print("ERROR: expected one 4kWh slot to start from, got {}".format(base))
        return True

    early = my_predbat.car_battery_early_slots(0, base, 0)
    if not early or len(early) != 2:
        print("ERROR: expected a chunk plus the reduced slot, got {}".format(early))
        return True
    chunk, reduced = early
    if not chunk.get("battery_early") or chunk["start"] != now or chunk["end"] != now + CAR_BATTERY_EARLY_MINUTES:
        print("ERROR: the chunk should run from now for {} minutes, got {}".format(CAR_BATTERY_EARLY_MINUTES, chunk))
        failed = True
    if abs(chunk["kwh"] - 2.0) > 0.001 or abs(reduced["kwh"] - 2.0) > 0.001:
        print("ERROR: expected 2kWh moved and 2kWh left, got {} and {}".format(chunk["kwh"], reduced["kwh"]))
        failed = True
    if reduced["end"] - reduced["start"] != 30 or reduced["start"] != base[0]["start"]:
        print("ERROR: the source slot should keep its start and shrink to 30 minutes, got {}".format(reduced))
        failed = True
    if base[0]["kwh"] != 4.0:
        print("ERROR: the base plan must not be edited in place")
        failed = True

    # A small slot is moved whole and disappears
    small = [dict(base[0], kwh=1.0, end=base[0]["start"] + 15)]
    early = my_predbat.car_battery_early_slots(0, small, 0)
    if not early or len(early) != 1 or abs(early[0]["kwh"] - 1.0) > 0.001 or early[0]["end"] - early[0]["start"] != 15:
        print("ERROR: a 1kWh slot should move whole as a 15 minute chunk, got {}".format(early))
        failed = True

    # Nothing to bring forward while a planned slot already covers now
    covering = [{"start": now - 10, "end": now + 50, "start_orig": now - 10, "kwh": 4.0, "average": 150.0, "cost": 600.0, "octopus": False}]
    if my_predbat.car_battery_early_slots(0, covering, 0):
        print("ERROR: a slot covering now should not get a chunk")
        failed = True

    return failed


def run_blocked_rules(my_predbat):
    """The hard rules: switches, plan, and no export or charge window anywhere in the chunk."""
    failed = False
    print("**** Running Test: battery early hard rules ****")

    base = setup_battery_early(my_predbat)
    now = my_predbat.minutes_now

    if my_predbat.car_battery_early_blocked(0, base):
        print("ERROR: nothing should block the base scenario, got {}".format(my_predbat.car_battery_early_blocked(0, base)))
        failed = True

    checks = []
    my_predbat.car_charging_battery_early = False
    checks.append(("switch off", "disabled", my_predbat.car_battery_early_blocked(0, base)))
    my_predbat.car_charging_battery_early = True

    my_predbat.car_charging_from_battery = False
    checks.append(("from battery off", "disabled", my_predbat.car_battery_early_blocked(0, base)))
    my_predbat.car_charging_from_battery = True

    my_predbat.car_charging_planned = [False]
    checks.append(("not planned", "not_planned", my_predbat.car_battery_early_blocked(0, base)))
    my_predbat.car_charging_planned = [True]

    checks.append(("octopus slot", "octopus", my_predbat.car_battery_early_blocked(0, [dict(base[0], octopus=True)])))

    my_predbat.isExporting = True
    checks.append(("exporting", "exporting", my_predbat.car_battery_early_blocked(0, base)))
    my_predbat.isExporting = False

    my_predbat.export_window_best = [{"start": now - 30, "end": now + 30, "average": 150.0}]
    my_predbat.export_limits_best = [0.0]
    checks.append(("in export window", "export_window", my_predbat.car_battery_early_blocked(0, base)))
    my_predbat.export_window_best = [{"start": now + 20, "end": now + 80, "average": 150.0}]
    checks.append(("export window within the chunk", "export_window", my_predbat.car_battery_early_blocked(0, base)))
    my_predbat.export_limits_best = [EXPORT_LIMIT_IDLE]
    checks.append(("idle export window", None, my_predbat.car_battery_early_blocked(0, base)))
    my_predbat.export_window_best = []
    my_predbat.export_limits_best = []

    saved_window, saved_limit = my_predbat.charge_window_best, my_predbat.charge_limit_best
    my_predbat.charge_window_best = [{"start": now - 30, "end": now + 30, "average": 150.0}]
    my_predbat.charge_limit_best = [25.0]
    checks.append(("in charge window", "charge_window", my_predbat.car_battery_early_blocked(0, base)))
    my_predbat.charge_limit_best = [0.0]
    checks.append(("disabled charge window", None, my_predbat.car_battery_early_blocked(0, base)))
    my_predbat.charge_window_best, my_predbat.charge_limit_best = saved_window, saved_limit

    for name, expected, got in checks:
        if got != expected:
            print("ERROR: {} should give {}, got {}".format(name, expected, got))
            failed = True
    return failed


def run_accepts_when_free(my_predbat):
    """Full battery, flat rates: the battery serves the slot anyway, so the chunk moves and is published."""
    failed = False
    print("**** Running Test: battery early moves free charge forward ****")

    base = setup_battery_early(my_predbat)
    saved_dashboard = copy.deepcopy(my_predbat.dashboard_values)
    try:
        lines = capture_log(my_predbat, my_predbat.plan_car_battery_early)
        slots = my_predbat.car_charging_slots[0]
        if not lines or "-> on" not in lines[-1]:
            print("ERROR: expected an 'on' decision, got {}".format(lines))
            failed = True
        if not slots or not slots[0].get("battery_early") or slots[0]["start"] != my_predbat.minutes_now:
            print("ERROR: expected the plan to start with a chunk now, got {}".format(slots))
            failed = True
        if abs(sum(slot["kwh"] for slot in slots) - sum(slot["kwh"] for slot in base)) > 0.01:
            print("ERROR: moving charge must not change the total, {} vs {}".format(slots, base))
            failed = True
        if my_predbat.car_battery_early_active.get(0) != base[0]["start_orig"]:
            print("ERROR: expected the source slot remembered, got {}".format(my_predbat.car_battery_early_active))
            failed = True

        mode = my_predbat.dashboard_values.get("sensor.predbat_car_charging_mode", {})
        if mode.get("state") != "now" or mode.get("attributes", {}).get("reason") != "battery_early":
            print("ERROR: expected car_charging_mode now/battery_early, got {}".format(mode))
            failed = True
        slot_sensor = my_predbat.dashboard_values.get("binary_sensor.predbat_car_charging_slot", {})
        if slot_sensor.get("state") != "on":
            print("ERROR: expected the car charging slot sensor on, got {}".format(slot_sensor))
            failed = True
    finally:
        my_predbat.dashboard_values.clear()
        my_predbat.dashboard_values.update(saved_dashboard)
    return failed


def run_rejects_when_dearer(my_predbat):
    """Empty battery and dear rates now: the slot is met from cheap import, so moving it costs - no chunk."""
    failed = False
    print("**** Running Test: battery early stays off when it costs ****")

    base = setup_battery_early(my_predbat, soc_kw=0.0)
    for minute in range(my_predbat.minutes_now, my_predbat.minutes_now + 4 * 60):
        my_predbat.rate_import[minute] = 300.0
    my_predbat.rate_import_base = my_predbat.rate_import.copy()

    lines = capture_log(my_predbat, my_predbat.plan_car_battery_early)
    if not lines or "-> off" not in lines[-1]:
        print("ERROR: expected an 'off' decision, got {}".format(lines))
        failed = True
    if my_predbat.car_charging_slots[0] != base or my_predbat.car_battery_early_active:
        print("ERROR: the plan should be left as it was, got {}".format(my_predbat.car_charging_slots[0]))
        failed = True
    return failed


def run_disabled_is_silent(my_predbat):
    """With the switch off nothing is scored, logged or changed."""
    failed = False
    print("**** Running Test: battery early off by default ****")

    base = setup_battery_early(my_predbat)
    my_predbat.car_charging_battery_early = False
    lines = capture_log(my_predbat, my_predbat.plan_car_battery_early)
    if lines or my_predbat.car_charging_slots[0] != base:
        print("ERROR: the switch off should do nothing, got {} and {}".format(lines, my_predbat.car_charging_slots[0]))
        failed = True
    return failed


def run_reapply_and_stop(my_predbat):
    """A running chunk survives fetch's rebuild, and an export window stops it and restores the plan."""
    failed = False
    print("**** Running Test: battery early across cycles ****")

    base = setup_battery_early(my_predbat)
    saved_dashboard = copy.deepcopy(my_predbat.dashboard_values)
    try:
        my_predbat.plan_car_battery_early()
        if not my_predbat.car_battery_early_active:
            print("ERROR: setup should leave the chunk running")
            return True

        # fetch rebuilds the plan from the rates, then puts the chunk back in front
        rebuilt = my_predbat.car_battery_early_reapply(0, list(base))
        if not rebuilt or not rebuilt[0].get("battery_early"):
            print("ERROR: the rebuilt plan should keep the chunk, got {}".format(rebuilt))
            failed = True
        if my_predbat.car_battery_early_base.get(0) != base:
            print("ERROR: reapply should remember the plan it was given")
            failed = True
        my_predbat.car_charging_slots = [rebuilt]

        # An export window arrives over now: the chunk stops and the price plan comes back
        now = my_predbat.minutes_now
        my_predbat.export_window_best = [{"start": now, "end": now + 60, "average": 150.0}]
        my_predbat.export_limits_best = [0.0]
        if my_predbat.car_battery_early_reapply(0, list(base)) != base:
            print("ERROR: reapply should not add a chunk over an export window")
            failed = True
        lines = capture_log(my_predbat, my_predbat.plan_car_battery_early)
        if not any("stopped (export_window)" in line for line in lines):
            print("ERROR: expected a stopped (export_window) line, got {}".format(lines))
            failed = True
        if my_predbat.car_charging_slots[0] != base or my_predbat.car_battery_early_active:
            print("ERROR: the price plan should be restored, got {}".format(my_predbat.car_charging_slots[0]))
            failed = True
        mode = my_predbat.dashboard_values.get("sensor.predbat_car_charging_mode", {})
        if mode.get("attributes", {}).get("reason") == "battery_early":
            print("ERROR: the published mode should no longer say battery_early, got {}".format(mode))
            failed = True
    finally:
        my_predbat.dashboard_values.clear()
        my_predbat.dashboard_values.update(saved_dashboard)
    return failed


def run_score_default_unchanged(my_predbat):
    """score_extra_load without car_charging_slots still scores the live car plan."""
    failed = False
    print("**** Running Test: score_extra_load default car slots ****")

    setup_battery_early(my_predbat)
    cache = {}
    default = my_predbat.score_extra_load({}, kernel_static_cache=cache, include_battery_value=True)
    explicit = my_predbat.score_extra_load({}, kernel_static_cache=cache, include_battery_value=True, car_charging_slots=my_predbat.car_charging_slots_model())
    no_car = my_predbat.score_extra_load({}, kernel_static_cache=cache, include_battery_value=True, car_charging_slots=[[]])
    if abs(default - explicit) > 0.001:
        print("ERROR: default and explicit car slots should score the same, got {} and {}".format(default, explicit))
        failed = True
    if abs(default - no_car) < 0.001:
        print("ERROR: dropping the car's 4kWh should change the score")
        failed = True
    return failed


def run_car_battery_early_tests(my_predbat):
    """Run every car battery early test.

    setup_battery_early rebinds a lot of shared state - the horizon, the step dicts, the plan and the whole
    car configuration - and my_predbat outlives this module, so put the attribute bindings back afterwards.
    """
    failed = False
    saved = dict(my_predbat.__dict__)
    try:
        failed |= run_slots_arithmetic(my_predbat)
        failed |= run_blocked_rules(my_predbat)
        failed |= run_accepts_when_free(my_predbat)
        failed |= run_rejects_when_dearer(my_predbat)
        failed |= run_disabled_is_silent(my_predbat)
        failed |= run_reapply_and_stop(my_predbat)
        failed |= run_score_default_unchanged(my_predbat)
    finally:
        my_predbat.__dict__.clear()
        my_predbat.__dict__.update(saved)
    if not failed:
        print("**** Car battery early tests passed ****")
    return failed
