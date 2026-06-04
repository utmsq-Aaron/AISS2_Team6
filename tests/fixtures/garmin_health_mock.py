"""
Mock Garmin Connect client for development and testing.

Generates deterministic, realistic health data without real Garmin credentials.
Data is seeded by date so the same date always returns the same values, but
each day looks different. Modelled on an active male ~74 kg, ~VO2max 52-55.

Enable via .env:
    GARMIN_MOCK_HEALTH=true
"""
import hashlib
from datetime import datetime, timedelta


def _seed(date_str: str, offset: int = 0) -> float:
    """Deterministic float in [0, 1) derived from date + offset."""
    key = f"{date_str}:{offset}".encode()
    return hashlib.md5(key).digest()[0] / 255.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _ts_ms(date_str: str, hour: int, minute: int = 0) -> int:
    """Local Unix timestamp in milliseconds for the given date + time."""
    dt = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}:00", "%Y-%m-%d %H:%M:%S")
    return int(dt.timestamp() * 1000)


def _date_range(start: str, end: str):
    d = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    while d <= end_dt:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


class MockGarminClient:
    """
    Drop-in replacement for garminconnect.Garmin.
    Implements the exact method signatures used in servers/garmin.py.
    """

    # ── Sleep ─────────────────────────────────────────────────────────────────

    def get_sleep_data(self, date: str) -> dict:
        total_secs = int(_lerp(6.5, 8.5, _seed(date, 0)) * 3600)
        awake_secs = int(_lerp(600, 1800, _seed(date, 3)))
        sleep_secs = total_secs - awake_secs
        deep_secs  = int(sleep_secs * _lerp(0.12, 0.22, _seed(date, 1)))
        rem_secs   = int(sleep_secs * _lerp(0.18, 0.28, _seed(date, 2)))
        light_secs = sleep_secs - deep_secs - rem_secs
        score      = round(_lerp(68, 95, _seed(date, 4)), 1)

        feedbacks = [
            "SLEEP_QUALITY_FAIR",
            "SLEEP_QUALITY_GOOD",
            "SLEEP_QUALITY_GOOD",
            "SLEEP_QUALITY_EXCELLENT",
        ]
        feedback = feedbacks[int(_seed(date, 5) * len(feedbacks))]

        return {
            "dailySleepDTO": {
                "sleepTimeSeconds":        total_secs,
                "deepSleepSeconds":        deep_secs,
                "lightSleepSeconds":       light_secs,
                "remSleepSeconds":         rem_secs,
                "awakeSleepSeconds":       awake_secs,
                "sleepScores":             {"overall": {"value": score}},
                "sleepScoreFeedback":      feedback,
                "averageSpO2Value":        round(_lerp(95.5, 98.8, _seed(date, 6)), 1),
                "averageRespirationValue": round(_lerp(13.5, 16.5, _seed(date, 7)), 1),
                "averageStressLevel":      round(_lerp(18, 38, _seed(date, 8)),  1),
                "averageHrvValue":         round(_lerp(45, 78, _seed(date, 9)),  1),
                "hmvValue":                round(_lerp(42, 74, _seed(date, 10)), 1),
            }
        }

    # ── Daily stats ───────────────────────────────────────────────────────────

    def get_stats(self, date: str) -> dict:
        dow = datetime.strptime(date, "%Y-%m-%d").weekday()  # 0 = Monday
        is_weekend = dow >= 5
        steps_base = 8500 if is_weekend else 10500
        steps = int(_lerp(steps_base * 0.7, steps_base * 1.35, _seed(date, 11)))
        rhr   = int(_lerp(50, 60, _seed(date, 12)))
        avg_stress = round(_lerp(20, 55, _seed(date, 13)), 1)

        qualifiers = ["CALM", "BALANCED", "STRESSFUL", "VERY_STRESSFUL"]
        qual_idx   = (
            0 if avg_stress < 26
            else 1 if avg_stress < 40
            else 2 if avg_stress < 60
            else 3
        )

        return {
            "totalSteps":               steps,
            "totalDistanceMeters":      round(steps * 0.78, 1),
            "activeKilocalories":       int(_lerp(200, 700, _seed(date, 14))),
            "totalKilocalories":        int(_lerp(1900, 3100, _seed(date, 15))),
            "restingHeartRate":         rhr,
            "minHeartRate":             rhr - 4,
            "maxHeartRate":             int(_lerp(130, 185, _seed(date, 16))),
            "averageStressLevel":       avg_stress,
            "maxStressLevel":           round(avg_stress + _lerp(10, 30, _seed(date, 17))),
            "stressQualifier":          qualifiers[qual_idx],
            "moderateIntensityMinutes": int(_lerp(0, 30, _seed(date, 18))),
            "vigorousIntensityMinutes": int(_lerp(0, 45, _seed(date, 19))),
            "floorsAscended":           round(_lerp(2, 18, _seed(date, 20))),
        }

    # ── Body Battery ──────────────────────────────────────────────────────────

    def get_body_battery(self, start_date: str, end_date: str = None) -> list:
        if end_date is None:
            end_date = start_date
        days = []
        for date in _date_range(start_date, end_date):
            start_val = int(_lerp(78, 96, _seed(date, 21)))
            end_val   = int(_lerp(22, 48, _seed(date, 22)))
            timeline  = []
            for h in range(24):
                for m in (0, 15, 30, 45):
                    frac  = (h * 60 + m) / (23 * 60 + 45)
                    noise = _lerp(-6, 6, _seed(date, 200 + h * 4 + m // 15))
                    val   = int(start_val - (start_val - end_val) * frac + noise)
                    val   = max(10, min(100, val))
                    timeline.append([_ts_ms(date, h, m), val])

            vals = [pt[1] for pt in timeline]
            days.append({
                "calendarDate":           date,
                "charged":                max(vals) - min(vals),
                "drained":                max(vals) - min(vals),
                "bodyBatteryValuesArray": timeline,
            })
        return days

    # ── HRV ───────────────────────────────────────────────────────────────────

    def get_hrv_data(self, date: str) -> dict:
        hrv = round(_lerp(48, 76, _seed(date, 30)), 1)
        idx = int(_seed(date, 31) * 5)
        statuses = ["balanced", "balanced", "balanced", "unbalanced", "low"]
        phrases  = [
            "HRV_BALANCED_2", "HRV_BALANCED_2", "HRV_BALANCED_3",
            "HRV_UNBALANCED_2", "HRV_LOW_1",
        ]
        return {
            "hrvSummary": {
                "lastNight5MinHighHrv":   hrv,
                "baselineLowUpper":       42.0,
                "baselineBalancedLow":    48.0,
                "baselineBalancedUpper":  72.0,
                "status":                 statuses[idx],
                "feedbackPhrase":         phrases[idx],
            }
        }

    # ── Heart Rate ────────────────────────────────────────────────────────────

    def get_heart_rates(self, date: str) -> dict:
        rhr      = int(_lerp(50, 60, _seed(date, 40)))
        timeline = []
        for h in range(24):
            for m in (0, 15, 30, 45):
                slot = h * 4 + m // 15
                if h < 6:
                    hr = int(_lerp(rhr - 5, rhr + 8, _seed(date, 300 + slot)))
                elif h < 8:
                    hr = int(_lerp(rhr + 5, rhr + 28, _seed(date, 400 + slot)))
                elif h < 18:
                    hr = int(_lerp(rhr + 10, rhr + 50, _seed(date, 500 + slot)))
                else:
                    hr = int(_lerp(rhr + 5, rhr + 22, _seed(date, 600 + slot)))
                timeline.append([_ts_ms(date, h, m), hr])

        all_hr = [pt[1] for pt in timeline]
        return {
            "heartRateValues": timeline,
            "restingHeartRate": rhr,
            "minHeartRate":     min(all_hr),
            "maxHeartRate":     max(all_hr),
        }

    # ── Stress ────────────────────────────────────────────────────────────────

    def get_stress_data(self, date: str) -> dict:
        timeline = []
        for h in range(24):
            for m in (0, 15, 30, 45):
                slot = h * 4 + m // 15
                if h < 6:
                    stress = -1  # sleeping — no measurement
                elif h < 9:
                    stress = int(_lerp(15, 35, _seed(date, 700 + slot)))
                elif h < 17:
                    stress = int(_lerp(20, 65, _seed(date, 800 + slot)))
                else:
                    stress = int(_lerp(10, 32, _seed(date, 900 + slot)))
                timeline.append([_ts_ms(date, h, m), stress])
        return {"stressValuesArray": timeline}

    # ── Steps ─────────────────────────────────────────────────────────────────

    def get_steps_data(self, date: str) -> list:
        total_steps = self.get_stats(date)["totalSteps"]
        active_buckets = sum(1 for h in range(7, 22) for _ in (0, 15, 30, 45))
        per_bucket = total_steps // max(active_buckets, 1)
        buckets = []
        for h in range(24):
            for m in (0, 15, 30, 45):
                end_m = m + 15
                end_h = h + end_m // 60
                end_m %= 60
                slot = h * 4 + m // 15
                if 7 <= h < 22:
                    variance = _lerp(0.4, 1.7, _seed(date, 1000 + slot))
                    steps = int(per_bucket * variance)
                    level = "active" if steps > 50 else "sedentary"
                else:
                    steps = 0
                    level = "sleeping" if h < 6 or h >= 22 else "sedentary"
                buckets.append({
                    "startGMT": f"{date}T{h:02d}:{m:02d}:00",
                    "endGMT":   f"{date}T{end_h:02d}:{end_m:02d}:00",
                    "steps":    steps,
                    "primaryActivityLevel": level,
                })
        return buckets

    # ── Training metrics ──────────────────────────────────────────────────────

    def get_max_metrics(self, date: str) -> list:
        return [{
            "generic":  {"vo2MaxPreciseValue": round(_lerp(50.0, 56.0, _seed(date, 80)), 1)},
            "cycling":  {"vo2MaxPreciseValue": round(_lerp(48.0, 54.0, _seed(date, 81)), 1)},
        }]

    def get_training_status(self, date: str) -> list:
        statuses = ["MAINTAINING", "PRODUCTIVE", "PEAKING", "RECOVERING"]
        idx = int(_seed(date, 82) * len(statuses))
        return [{
            "latestTrainingStatus": {
                "trainingStatus": statuses[idx],
                "trainingLoadBalance": {
                    "shortTermTrainingLoad": round(_lerp(55, 155, _seed(date, 83)), 1),
                    "longTermTrainingLoad":  round(_lerp(75, 185, _seed(date, 84)), 1),
                },
            }
        }]

    def get_race_predictions(self) -> list:
        return [{
            "time5K":            int(_lerp(1180, 1480, _seed("pred", 0))),   # ~20–25 min
            "time10K":           int(_lerp(2460, 3120, _seed("pred", 1))),   # ~41–52 min
            "timeHalfMarathon":  int(_lerp(5340, 6900, _seed("pred", 2))),   # ~89–115 min
            "timeMarathon":      int(_lerp(10900, 14800, _seed("pred", 3))), # ~3:02–4:07
        }]

    def get_training_readiness(self, date: str) -> list:
        score = int(_lerp(52, 90, _seed(date, 90)))
        label = (
            "PRIME"    if score >= 85 else
            "HIGH"     if score >= 70 else
            "MODERATE" if score >= 55 else
            "LOW"
        )
        return [{"score": score, "levelLabel": label}]

    # ── Body composition ──────────────────────────────────────────────────────

    def get_body_composition(self, start: str, end: str) -> dict:
        measurements = []
        for date in _date_range(start, end):
            weight_g = int(_lerp(72500, 76500, _seed(date, 100)))
            measurements.append({
                "calendarDate": date,
                "weight":       weight_g,
                "bmi":          round(weight_g / 1000 / (1.80 ** 2), 1),
                "bodyFat":      round(_lerp(12.0, 17.5, _seed(date, 101)), 1),
                "muscleMass":   int(weight_g * 0.42),
                "boneMass":     int(weight_g * 0.04),
            })
        return {"dateWeightList": measurements}
