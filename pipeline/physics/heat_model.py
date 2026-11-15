"""Required heat model: surface daily/yearly swings decay exponentially with depth.
Skin depth = sqrt(kappa * period / pi), kappa = (thermal_inertia / vol_heat_cap)^2.
Assumes uniform ground. Out: temp_mean (~surface annual mean) and temp_swing per band.
Step 4; plan s10. All constants come from config `processing:`.
"""
import math

from ..common.config import get_processing


def skin_depth_m(thermal_inertia, period_s, vol_heat_cap):
    return math.sqrt((thermal_inertia / vol_heat_cap) ** 2 * period_s / math.pi)


def mars_surface(lat, thermal_inertia, params=None):
    """Mars surface (mean K, diurnal amp K, annual amp K) from latitude + thermal inertia."""
    s = (params or get_processing())["heat_model"]["surface"]
    sl = math.sin(math.radians(lat))
    mean = s["mean_eq_k"] - s["mean_pole_drop_k"] * sl ** 2
    diurnal = s["diurnal_amp_ref_k"] * math.sqrt(s["ti_ref"] / max(thermal_inertia, 1.0))
    return mean, diurnal, s["annual_amp_k"] * abs(sl)


def band_mid_depth_m(band, params=None):
    top, bottom = (params or get_processing())["band_depth_m"][band]
    return (top + bottom) / 2 if bottom is not None else top * 1.5


def band_top_m(band, params=None):
    return (params or get_processing())["band_depth_m"][band][0]


def temps_at_depth(mean_k, diurnal_amp_k, annual_amp_k, thermal_inertia, depth_m,
                   *, year_days, day_hours, vol_heat_cap):
    """Return (temp_mean, temp_swing) at a depth. Swing = peak-to-peak of both cycles."""
    dd = skin_depth_m(thermal_inertia, day_hours * 3600, vol_heat_cap)
    dy = skin_depth_m(thermal_inertia, year_days * 86400, vol_heat_cap)
    swing = 2 * (diurnal_amp_k * math.exp(-depth_m / dd) + annual_amp_k * math.exp(-depth_m / dy))
    return mean_k, swing


def mars_band_temps(lat, thermal_inertia, band, params=None):
    p = params or get_processing()
    h = p["heat_model"]
    ti = thermal_inertia if thermal_inertia == thermal_inertia else h["default_thermal_inertia"]
    mean, da, ya = mars_surface(lat, ti, p)
    return temps_at_depth(mean, da, ya, ti, band_mid_depth_m(band, p),
                          year_days=h["mars_year_days"], day_hours=h["mars_sol_hours"],
                          vol_heat_cap=h["volumetric_heat_capacity"])


def earth_band_temps(mean_k, annual_range_k, diurnal_amp_k, thermal_inertia, band, params=None):
    p = params or get_processing()
    h = p["earth_heat_model"]
    ti = thermal_inertia if thermal_inertia == thermal_inertia else h["default_thermal_inertia"]
    return temps_at_depth(mean_k, diurnal_amp_k, annual_range_k / 2, ti, band_mid_depth_m(band, p),
                          year_days=h["year_days"], day_hours=h["day_hours"],
                          vol_heat_cap=h["volumetric_heat_capacity"])
