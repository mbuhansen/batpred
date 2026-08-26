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

    # Keep the plan's own charge window clear of the candidate windows below - a charge window with a
    # zero target over the first half hour makes that one window import, which is a property of the
    # scenario rather than of the placement being measured
    my_predbat.charge_limit_best = [0.0]
    my_predbat.charge_window_best = [{"start": my_predbat.minutes_now + 20 * 60, "end": my_predbat.minutes_now + 20 * 60 + 30, "average": import_rate}]
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


def capture_scoring(my_predbat):
    """Run the diagnostic with self.log captured, returning the scoring lines it emitted.

    my_predbat has no log buffer of its own, so wrap the method. An earlier version of this test read a
    log_messages attribute that does not exist, which made every assertion in it vacuous.
    """
    captured = []
    original = my_predbat.log

    def collect(message):
        """Record the message, then let the real logger have it."""
        captured.append(str(message))
        return original(message)

    my_predbat.log = collect
    try:
        my_predbat.log_car_window_scoring()
    finally:
        my_predbat.log = original
    return [line for line in captured if "window scoring" in line]


def run_diagnostic_gates(my_predbat):
    """It only runs where it can say something the import rate cannot."""
    failed = False
    print("**** Running Test: window scoring gates ****")

    setup_scoring(my_predbat)

    if not capture_scoring(my_predbat):
        print("ERROR: the diagnostic logged nothing when it should have run")
        failed = True

    my_predbat.car_charging_from_battery = False
    if capture_scoring(my_predbat):
        print("ERROR: the diagnostic ran with car_charging_from_battery off")
        failed = True
    my_predbat.car_charging_from_battery = True

    # An empty charge plan is still a plan and must not stop it
    saved_limit = my_predbat.charge_limit_best
    my_predbat.charge_limit_best = []
    if not capture_scoring(my_predbat):
        print("ERROR: an empty charge plan should not stop the diagnostic")
        failed = True
    my_predbat.charge_limit_best = saved_limit

    # Without the live forecast there is nothing to score against
    saved_load = my_predbat.load_minutes_step
    my_predbat.load_minutes_step = {}
    if capture_scoring(my_predbat):
        print("ERROR: the diagnostic ran without the live load forecast")
        failed = True
    my_predbat.load_minutes_step = saved_load

    return failed


def run_diagnostic_like_for_like(my_predbat):
    """On flat rates no window is genuinely better, so the reported delta must be about zero.

    Guards the comparison itself: scoring the best window against the plan's pick priced at its raw import
    rate produced a steady ~100 ore/kWh "saving" that was only restating that battery energy is cheaper
    than importing - true wherever the charge is put, and nothing to do with the placement.
    """
    failed = False
    print("**** Running Test: window scoring compares like for like ****")

    ready = setup_scoring(my_predbat)
    now = my_predbat.minutes_now

    # Two windows an hour apart, both clear of the first hour. Load placed close to now is partly met by
    # import, which is a real property of the model rather than of the placement, so comparing across that
    # boundary would measure the wrong thing.
    cache = {}
    baseline = my_predbat.score_extra_load({}, kernel_static_cache=cache, include_battery_value=True)
    scores = []
    for offset in (120, 180):
        extra, kwh = my_predbat.car_window_load_delta(0, {"start": now + offset, "end": now + offset + 60, "average": 150.0}, ready)
        scores.append((my_predbat.score_extra_load(extra, kernel_static_cache=cache, include_battery_value=True) - baseline) / kwh)

    if abs(scores[0] - scores[1]) > 0.01:
        print("ERROR: on flat rates two later windows should score the same, got {} and {}".format(scores[0], scores[1]))
        failed = True

    # And the logged delta must be between two scored prices, not a scored price against an import rate
    lines = capture_scoring(my_predbat)
    if not lines:
        print("ERROR: the diagnostic logged nothing")
        return True
    line = lines[-1]
    currency = my_predbat.currency_symbols[1]
    parts = line.split("scored ")
    if len(parts) != 3:
        print("ERROR: expected both sides of the comparison to be scored, got: {}".format(line))
        return True
    picked_score = float(parts[1].split(currency)[0])
    best_score = float(parts[2].split(currency)[0])
    delta = float(line.split("delta ")[1].split(currency)[0])
    if abs(delta - (best_score - picked_score)) > 0.02:
        print("ERROR: delta {} is not the difference of the two scored prices ({} - {}) in: {}".format(delta, best_score, picked_score, line))
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
        failed |= run_diagnostic_like_for_like(my_predbat)
        failed |= run_diagnostic_load_delta(my_predbat)
        failed |= run_diagnostic_finds_cheaper(my_predbat)
    finally:
        my_predbat.__dict__.clear()
        my_predbat.__dict__.update(saved)
    if not failed:
        print("**** Car window scoring tests passed ****")
    return failed
