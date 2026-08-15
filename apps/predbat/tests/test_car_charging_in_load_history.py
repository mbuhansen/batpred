# -----------------------------------------------------------------------------
# Predbat Home Battery System
# Copyright Trefor Southwell 2026 - All Rights Reserved
# This application maybe used for personal use only and not for commercial use
# -----------------------------------------------------------------------------
# fmt off
# pylint: disable=consider-using-f-string
# pylint: disable=line-too-long
# pylint: disable=attribute-defined-outside-init
from prediction import Prediction
from tests.test_infra import reset_rates2, reset_inverter


def build_prediction(my_predbat, pv_amount=0.0, load_amount=1.0):
    """
    Build a Prediction over a flat PV/load profile, returning (prediction, pv_step, load_step)
    """
    pv_step = {}
    load_step = {}
    pv10_step = {}
    load10_step = {}
    for minute in range(0, my_predbat.forecast_minutes, 5):
        pv_step[minute] = pv_amount / (60 / 5)
        load_step[minute] = load_amount / (60 / 5)
        pv10_step[minute] = 0
        load10_step[minute] = 0
    return Prediction(my_predbat, pv_step, pv10_step, load_step, load10_step), pv_step, load_step


def run_one(my_predbat, in_load_history, reported_load=True, octopus=False, average=0.0):
    """
    Run a single prediction with one car slot, returning (import_kwh_house, metric, car_soc_next)
    """
    my_predbat.num_cars = 1
    my_predbat.car_energy_reported_load = reported_load
    my_predbat.car_charging_in_load_history = in_load_history
    my_predbat.car_charging_loss = 1.0
    my_predbat.car_charging_soc = [0, 0, 0, 0]
    my_predbat.car_charging_limit = [50, 100, 100, 100]
    my_predbat.car_charging_slots[0] = [{"start": my_predbat.minutes_now, "end": my_predbat.minutes_now + 300, "kwh": 10.0, "average": average, "octopus": octopus}]

    prediction, _, _ = build_prediction(my_predbat)
    result = prediction.run_prediction([], [], [], [], False, end_record=my_predbat.end_record, save=None)
    metric = result[0]
    import_kwh_house = result[2]
    car_charging_soc_next = result[12]
    return import_kwh_house, metric, car_charging_soc_next[0]


def run_car_charging_in_load_history_tests(my_predbat):
    """
    Test that planned car slots stop adding load when the car energy is already in the load history
    """
    failed = False
    print("**** Running Car charging in load history tests ****")

    reset_inverter(my_predbat)
    reset_rates2(my_predbat, 10.0, 5.0)

    saved = {
        name: getattr(my_predbat, name)
        for name in ["num_cars", "car_energy_reported_load", "car_charging_in_load_history", "car_charging_loss", "car_charging_soc", "car_charging_limit", "car_charging_slots", "car_charging_from_battery", "soc_kw", "soc_max", "prediction_kernel_enable"]
    }
    # Run against the Python engine - the kernel mirror of this guard is covered by test_kernel_parity
    my_predbat.prediction_kernel_enable = False
    my_predbat.car_charging_from_battery = True
    my_predbat.soc_kw = 0
    my_predbat.soc_max = 10.0
    my_predbat.car_charging_slots = [[], [], [], []]

    # Test 1: the planned slot's energy is not added a second time when it is already in the load history.
    # The car draws 10kWh over 5 hours, so the whole difference must be exactly that 10kWh.
    import_off, _, soc_off = run_one(my_predbat, in_load_history=False)
    import_on, _, soc_on = run_one(my_predbat, in_load_history=True)
    difference = import_off - import_on
    if abs(difference - 10.0) >= 0.01:
        print("ERROR: House import should drop by the full car slot (10.0 kWh) when it is already in the load history, got {}".format(difference))
        failed = True

    # Test 2: the car is still modelled - its SoC must advance identically, the guard only stops the load being
    # double counted. A naive fix that skipped the whole car block would break this.
    if abs(soc_off - soc_on) >= 0.001:
        print("ERROR: Car SoC should be unaffected by car_charging_in_load_history, got {} vs {}".format(soc_off, soc_on))
        failed = True

    # Test 3: with the car outside the CT clamp the bypass branch runs instead, so the switch changes nothing.
    # (fetch_config_options forces the switch Off in this configuration, this pins the prediction side.)
    import_bypass_off, metric_bypass_off, _ = run_one(my_predbat, in_load_history=False, reported_load=False)
    import_bypass_on, metric_bypass_on, _ = run_one(my_predbat, in_load_history=True, reported_load=False)
    if abs(import_bypass_off - import_bypass_on) >= 0.001 or abs(metric_bypass_off - metric_bypass_on) >= 0.001:
        print("ERROR: With car_energy_reported_load Off the guard must have no effect, got import {} vs {}, metric {} vs {}".format(import_bypass_off, import_bypass_on, metric_bypass_off, metric_bypass_on))
        failed = True

    # Test 4: the IOG beyond-cap premium is still charged. car_amount_premium must keep accumulating even when
    # the load is not added, because the grid import still contains the car when it comes from history.
    _, metric_premium_on, _ = run_one(my_predbat, in_load_history=True, octopus=True, average=40.0)
    _, metric_no_premium, _ = run_one(my_predbat, in_load_history=True, octopus=True, average=0.0)
    if metric_premium_on <= metric_no_premium:
        print("ERROR: IOG beyond-cap premium should still be charged with the guard on, got {} vs {}".format(metric_premium_on, metric_no_premium))
        failed = True

    for name, value in saved.items():
        setattr(my_predbat, name, value)

    return failed
