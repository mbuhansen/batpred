# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init

"""Tests for the car charging window scoring diagnostic.

plan_car_charging orders candidate windows by import rate, which is not what the charge costs when the
home battery serves the car. log_car_window_scoring measures the difference and logs it - it must never
change the plan, which is what most of this pins.
"""

from prediction import Prediction
from tests.test_infra import reset_rates, reset_inverter


def setup_scoring(my_predbat, import_rate=150.0, export_rate=5.0, forecast_hours=24):
    """Put my_predbat into a state where log_car_window_scoring can run."""
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

    my_predbat.charge_limit_best = [0.0]
    my_predbat.charge_window_best = [{"start": my_predbat.minutes_now, "end": my_predbat.minutes_now + 30, "average": import_rate}]
    my_predbat.export_window_best = []
    my_predbat.export_limits_best = []

    my_predbat.soc_max = 50.0
    my_predbat.soc_kw = 50.0
    my_predbat.battery_rate_max_discharge = 5 / 60.0
    my_predbat.battery_rate_max_charge = 5 / 60.0
    # reset_inverter leaves a 1kW inverter, which the car's 4kW cannot come through - the battery would
    # look useless for reasons that have nothing to do with what is being measured
    my_predbat.inverter_limit = 10 / 60.0
    my_predbat.export_limit = 10 / 60.0

    my_predbat.num_cars = 1
    my_predbat.car_charging_from_battery = True
    my_predbat.car_charging_battery_size = [50.0]
    my_predbat.car_charging_limit = [4.0]
    my_predbat.car_charging_soc = [0.0]
    my_predbat.car_charging_soc_next = [None]
    my_predbat.car_charging_rate = [4.0]
    my_predbat.car_charging_loss = 1.0
    my_predbat.car_charging_plan_max_price = [0]
    my_predbat.car_charging_plan_smart = [True]
    my_predbat.car_charging_now = [False]

    # A ready time twelve hours out, and hourly candidate windows up to it
    ready = my_predbat.minutes_now + 12 * 60
    my_predbat.car_charging_plan_time = ["{:02d}:{:02d}:00".format((ready // 60) % 24, ready % 60)]
    my_predbat.low_rates = []
    for hour in range(0, 12):
        start = my_predbat.minutes_now + hour * 60
        my_predbat.low_rates.append({"start": start, "end": start + 60, "average": my_predbat.rate_import.get(start, import_rate)})

    my_predbat.car_charging_slots = [my_predbat.plan_car_charging(0, my_predbat.low_rates)]
    return ready


def run_diagnostic_is_read_only(my_predbat):
    """The diagnostic must not change the plan it is measuring."""
    failed = False
    print("**** Running Test: window scoring is read only ****")

    setup_scoring(my_predbat)
    before_slots = [list(slots) for slots in my_predbat.car_charging_slots]
    before_limit = list(my_predbat.charge_limit_best)
    before_export = list(my_predbat.export_limits_best)

    my_predbat.log_car_window_scoring()

    if my_predbat.car_charging_slots != before_slots:
        print("ERROR: the diagnostic changed car_charging_slots")
        failed = True
    if my_predbat.charge_limit_best != before_limit or my_predbat.export_limits_best != before_export:
        print("ERROR: the diagnostic changed the battery plan")
        failed = True

    return failed


def run_diagnostic_gates(my_predbat):
    """It only runs where it can say something the import rate cannot."""
    failed = False
    print("**** Running Test: window scoring gates ****")

    setup_scoring(my_predbat)
    logged_before = len(my_predbat.log_messages) if hasattr(my_predbat, "log_messages") else None

    my_predbat.car_charging_from_battery = False
    my_predbat.log_car_window_scoring()
    my_predbat.car_charging_from_battery = True

    # With no previous plan there is nothing to score against
    saved_limit = my_predbat.charge_limit_best
    my_predbat.charge_limit_best = []
    my_predbat.log_car_window_scoring()
    my_predbat.charge_limit_best = saved_limit

    # Neither of those may have logged a scoring line
    if logged_before is not None and any("window scoring" in str(line) for line in my_predbat.log_messages[logged_before:]):
        print("ERROR: the diagnostic ran while gated off")
        failed = True

    return failed


def run_diagnostic_load_delta(my_predbat):
    """car_window_load_delta places the charge the window would actually book."""
    failed = False
    print("**** Running Test: window load delta ****")

    ready = setup_scoring(my_predbat)
    now = my_predbat.minutes_now

    # A full hour at 4kW, but the car only needs 4.0kWh in total, so that is what it books
    result = my_predbat.car_window_load_delta(0, {"start": now, "end": now + 60, "average": 150.0}, ready)
    if not result:
        print("ERROR: expected a load delta for a window the car can use")
        return True
    extra, kwh = result
    if abs(sum(extra.values()) - kwh) > 0.0001:
        print("ERROR: the placed load {} does not match the booked {}kWh".format(sum(extra.values()), kwh))
        failed = True
    if abs(kwh - 4.0) > 0.0001:
        print("ERROR: expected 4.0kWh booked, got {}".format(kwh))
        failed = True

    # A window past the ready time books nothing
    if my_predbat.car_window_load_delta(0, {"start": ready, "end": ready + 60, "average": 150.0}, ready):
        print("ERROR: a window after the ready time should book nothing")
        failed = True

    # A full car needs nothing
    my_predbat.car_charging_soc = [4.0]
    if my_predbat.car_window_load_delta(0, {"start": now, "end": now + 60, "average": 150.0}, ready):
        print("ERROR: a full car should book nothing")
        failed = True

    return failed


def run_diagnostic_finds_cheaper(my_predbat):
    """It reports a price below the window's import rate when the battery is the cheaper source.

    Every hour the car can reach is expensive and the cheap rate only arrives after the ready time, so
    the car cannot use it but the battery can be refilled at it - which is the case the import-rate sort
    cannot see.
    """
    failed = False
    print("**** Running Test: window scoring finds the cheaper source ****")

    ready = setup_scoring(my_predbat)
    for minute in range(my_predbat.minutes_now, my_predbat.minutes_now + my_predbat.forecast_minutes):
        my_predbat.rate_import[minute] = 150.0 if minute < ready else 20.0
    my_predbat.rate_scan(my_predbat.rate_import, print=False)
    my_predbat.rate_import_base = my_predbat.rate_import.copy()

    window = {"start": my_predbat.minutes_now, "end": my_predbat.minutes_now + 60, "average": 150.0}
    result = my_predbat.car_window_load_delta(0, window, ready)
    if not result:
        print("ERROR: expected a load delta")
        return True
    extra, kwh = result

    cache = {}
    baseline = my_predbat.score_extra_load({}, kernel_static_cache=cache, include_battery_value=True)
    metric = my_predbat.score_extra_load(extra, kernel_static_cache=cache, include_battery_value=True)
    cost = (metric - baseline) / kwh

    if cost >= window["average"]:
        print("ERROR: with the battery full and cheap refill ahead, expected below {}, got {}".format(window["average"], cost))
        failed = True

    # Without the battery credit the same charge looks free, which is the trap the correction exists for
    uncredited_base = my_predbat.score_extra_load({}, kernel_static_cache=cache)
    uncredited = my_predbat.score_extra_load(extra, kernel_static_cache=cache)
    if (uncredited - uncredited_base) / kwh >= cost:
        print("ERROR: include_battery_value should raise the price, not lower it")
        failed = True

    return failed


def run_car_window_scoring_tests(my_predbat):
    """Run every car window scoring test.

    setup_scoring rebinds a lot of shared state - the horizon, the step dicts, the plan and the whole car
    configuration - and my_predbat outlives this module, so put the attribute bindings back afterwards.
    """
    failed = False
    saved = dict(my_predbat.__dict__)
    try:
        failed |= run_diagnostic_is_read_only(my_predbat)
        failed |= run_diagnostic_gates(my_predbat)
        failed |= run_diagnostic_load_delta(my_predbat)
        failed |= run_diagnostic_finds_cheaper(my_predbat)
    finally:
        my_predbat.__dict__.clear()
        my_predbat.__dict__.update(saved)
    if not failed:
        print("**** Car window scoring tests passed ****")
    return failed
