import requests
from datetime import datetime

class EVCCAPI:
    """
    Lokal EVCC integration til udtræk af elbilens ladeinformationer
    """
    def __init__(self, base_url, log):
        self.base_url = base_url  # fx "http://evcc.local:7070/api"
        self.log = log

    def get_vehicle_info(self):
        """Henter bilens ladeinfo fra EVCC."""
        url = f"{self.base_url}/vehicles"
        try:
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            self.log(f"EVCC fejl: {e}")
            return {}

        # Antager kun én bil
        if data and "vehicles" in data and len(data["vehicles"]) > 0:
            v = data["vehicles"][0]
            return {
                "model": v.get("title"),
                "battery_size": v.get("capacity"),
                "soc": v.get("soc"),
                "charging": v.get("connected"),
                "charge_power": v.get("chargePower"),
            }
        return {}

    def get_charge_sessions(self):
        """Henter igangværende og historiske lade-sessioner."""
        url = f"{self.base_url}/charge"
        try:
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            self.log(f"EVCC fejl: {e}")
            return []

        # Returner igangværende session, fx
        session = data.get("session", {})
        if session:
            return [{
                "start": session.get("start"),
                "duration": session.get("duration"),
                "energy": session.get("energy"),
                "charge_power": session.get("chargePower"),
            }]
        return []

    def get_intelligent_vehicle(self):
        """
        Returnerer et dict med de mest relevante ladeparametre,
        så du kan bruge dem i stedet for Octopus-data.
        """
        vi = self.get_vehicle_info()
        return {
            "vehicleBatterySizeInKwh": vi.get("battery_size"),
            "chargePointPowerInKw": vi.get("charge_power"),
            "weekdayTargetTime": None,            # Ikke understøttet af EVCC
            "weekdayTargetSoc": vi.get("soc"),
            "weekendTargetTime": None,
            "weekendTargetSoc": None,
            "minimumSoc": None,
            "maximumSoc": None,
            "suspended": not vi.get("charging"),
            "model": vi.get("model"),
            "provider": "EVCC",
            "status": "LIVE" if vi.get("charging") else "IDLE",
        }

# Eksempel på brug:
if __name__ == "__main__":
    def log(msg):
        print(msg)

    evcc_api = EVCCAPI(base_url="http://evcc.local:7070/api", log=log)
    vehicle_info = evcc_api.get_intelligent_vehicle()
    print("Bil info fra EVCC:", vehicle_info)
    sessions = evcc_api.get_charge_sessions()
    print("Ladesessioner fra EVCC:", sessions)