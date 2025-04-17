# pylint: disable=invalid-name, missing-class-docstring

import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from clone_client.state_store.proto.state_store_pb2 import MagneticSensor
from scipy import interpolate

FH3D04_XY_MV_PER_MT = 54.0  # mV / mT for x and y axes
FH3D04_Z_MV_PER_MT = 94.0  # mv / mT for z axis

FH3D04_TEMP_DIGIT2CELS = 0.072484471  # deg C / digit


# CONSTANTS
FH3D04_BASIC_GAIN_XY = 128
FH3D04_BASIC_GAIN_Z = 64
FH3D04_BASIC_DEC_LEN = 512
FH3D04_BASIC_SUPPLY = 2.6


def remap_axes(b_arr: np.ndarray):
    return -b_arr[:, [0, 2, 1]]


def remap_pixels(
    pixels: list[MagneticSensor.MagneticPixel],
) -> list[MagneticSensor.MagneticPixel]:
    return [
        pixels[3],
        pixels[2],
        pixels[0],
        pixels[1],
    ]


class NaiveMappingEstimatorBase:
    @dataclass
    class Config:
        t_offset: int = 4000
        dec_len: int = FH3D04_BASIC_DEC_LEN
        gain_xy: int = FH3D04_BASIC_GAIN_XY
        gain_z: int = FH3D04_BASIC_GAIN_Z
        supply: float = FH3D04_BASIC_SUPPLY

    def __init__(self, config: Config) -> None:
        self._config = config

        self.t_dec_len_factor = FH3D04_BASIC_DEC_LEN / config.dec_len

        self.h_config_ratio_z = 1 / (
            config.gain_z
            / FH3D04_BASIC_GAIN_Z
            * config.dec_len
            / FH3D04_BASIC_DEC_LEN
            * config.supply
            / FH3D04_BASIC_SUPPLY
        )

        self.h_config_factor_xy = 1 / (
            config.gain_xy
            / FH3D04_BASIC_GAIN_XY
            * config.dec_len
            / FH3D04_BASIC_DEC_LEN
            * config.supply
            / FH3D04_BASIC_SUPPLY
        )

    def _calculate_temp(self, t_val: int) -> tuple[float, float]:
        """Returns temperature in Celcius degrees from raw temperature"""
        t_val_pp = t_val - self._config.t_offset
        t_val_pp_prime = t_val_pp * self.t_dec_len_factor
        t_val_deg_c = t_val_pp_prime * FH3D04_TEMP_DIGIT2CELS + 25.0
        return t_val_deg_c, t_val_pp_prime

    def _s_z(self, t_val_pp_prime: float) -> float:
        return (
            2.6029e-14 * t_val_pp_prime**3
            + 5.6780e-10 * t_val_pp_prime**2
            - 4.3553e-6 * t_val_pp_prime
            + 0.011187
        )

    def _s_xy(self, t_val_pp_prime: float) -> float:
        return (
            2.4864e-14 * t_val_pp_prime**3
            + 5.4240e-10 * t_val_pp_prime**2
            + 4.1604e-6 * t_val_pp_prime
            + 0.010687
        )

    def _calculate_teslas_z(self, h_val: float, t_val_pp_prime):
        h_1 = h_val * self.h_config_ratio_z
        h_2 = h_1 * self._s_z(t_val_pp_prime)

        return h_2

    def _calculate_teslas_xy(self, h_val: float, t_val_pp_prime):
        h_1 = h_val * self.h_config_factor_xy
        h_2 = h_1 * self._s_xy(t_val_pp_prime)

        return h_2

    def calculate_sensor(
        self, x: float, y: float, z: float, temperature: float
    ) -> tuple[float, np.ndarray]:
        t, t_pp = self._calculate_temp(temperature)
        pixel_bs = [
            self._calculate_teslas_xy(x, t_pp),
            self._calculate_teslas_xy(y, t_pp),
            self._calculate_teslas_z(z, t_pp),
        ]

        return t, np.array(pixel_bs)


class Interpol:
    def __init__(
        self,
        mapping_path: str,
        filter_outlier_population: int = 10,
        filter_iir_new_sample_weight: float = 0.3,
        filter_outlier_sigma: float = 3.0,
        use_filter_outliers: bool = True,
        use_filter_iir: bool = True,
        t_offset: int = 4000,
        gain_xy: float = 1024,
        gain_z: float = 512,
    ) -> None:
        mapping = self._load_mapping(Path(mapping_path))
        self.interpolators = [
            interpolate.RBFInterpolator(
                np.array([B for _, B in mapping[idx]]),
                [(angle[0], angle[1]) for angle, _ in mapping[idx]],
                kernel="linear",
            )
            for idx in range(15)
            if mapping[idx] is not None
        ]

        self._filter_outliers_population: int = filter_outlier_population
        self.filter_outliers_sigma: float = filter_outlier_sigma
        self.use_filter_outliers: bool = use_filter_outliers
        self.use_filter_iir: bool = use_filter_iir

        if 1.0 < filter_iir_new_sample_weight < 0.0:
            raise ValueError("IIR new sample weight must be between 0.0 and 1.0")

        self._filt_samples = deque(maxlen=self._filter_outliers_population)
        self._iir_new_sample_weight = filter_iir_new_sample_weight
        self._iir_state: Optional[np.ndarray] = None

        self.estimator = NaiveMappingEstimatorBase(
            NaiveMappingEstimatorBase.Config(
                t_offset=t_offset,
                dec_len=FH3D04_BASIC_DEC_LEN,
                gain_xy=gain_xy,
                gain_z=gain_z,
                supply=FH3D04_BASIC_SUPPLY,
            )
        )

    def _load_mapping(
        self, map_path: Path
    ) -> dict[int, Optional[list[tuple[tuple[float, float], np.ndarray]]]]:
        """Returns jnt_nr -> (angles, B)"""
        with map_path.open("r") as fp:
            map_ = json.load(fp)
        return {
            int(snsr_nr): (
                [
                    ((float(ang[0]), float(ang[1])), np.array(B).ravel())
                    for ang, B in snsr_map
                ]
                if snsr_map is not None
                else None
            )
            for snsr_nr, snsr_map in map_.items()
        }

    def filter_outliers(
        self, arr_flat: np.ndarray, sig_mul: float = 3.0
    ) -> Optional[np.ndarray]:
        if len(self._filt_samples) < self._filter_outliers_population:
            self._filt_samples.append(
                arr_flat.copy()
            )  # when queue is full start elimination and filtering
            return None
        b_population = np.array(self._filt_samples)
        means = np.mean(b_population, axis=0)
        sigma = sig_mul * np.std(b_population, axis=0)
        arr_flat_filtered = arr_flat.clip(
            min=means - sigma, max=means + sigma
        )  # substitute outlier with last measurement

        return arr_flat_filtered

    def _filter_iir(self, arr_flat: np.ndarray) -> np.ndarray:
        if self._iir_state is None:
            filt_bs = np.array(self._filt_samples)
            self._iir_state = np.mean(filt_bs, axis=0)
            self._iir_state: np.ndarray  # pyright doesnt catch it
        arr_flat_filtered = np.average(
            np.stack([arr_flat, self._iir_state], axis=0),
            axis=0,
            weights=[self._iir_new_sample_weight, 1.0 - self._iir_new_sample_weight],
        )
        self._iir_state = arr_flat_filtered
        return arr_flat_filtered

    def get_angles(self, sensors: Sequence[MagneticSensor]) -> Optional[np.ndarray]:
        """
        Returns angles in degrees for each sensor.
        """
        if len(sensors != len(self.interpolators)):
            raise ValueError(
                f"Number of sensors ({len(sensors)}) does not match number of interpolators ({len(self.interpolators)})"
            )

        no_sensors = len(self.interpolators)
        angles = np.zeros((no_sensors, 2), dtype=np.float32)
        B_tot = np.zeros((no_sensors, 4, 3), dtype=np.float32)

        # Process each sensor
        for idx, sensor in enumerate(sensors):
            B = np.zeros((4, 3), dtype=np.float32)
            pixels = remap_pixels(sensor.pixels)
            for pidx, pixel in enumerate(pixels):
                _, B_pixel = self.estimator.calculate_sensor(
                    pixel.x, pixel.y, pixel.z, sensor.temperature
                )
                B[pidx] = B_pixel

            B = remap_axes(B)
            B_tot[idx] = B

        # Filter outliers and apply IIR filter
        B_flat_filtered = np.asarray([b.ravel() for b in B_tot])
        if self.use_filter_outliers:
            B_flat_filtered = self.filter_outliers(
                B_flat_filtered, self.filter_outliers_sigma
            )
            if B_flat_filtered is None:
                return None

            self._filt_samples.append(B_flat_filtered)

        if self.use_filter_iir:
            B_flat_filtered = self._filter_iir(B_flat_filtered)

        # Interpolate angles
        for bidx, B_sens in enumerate(B_flat_filtered):
            angle = self.interpolators[bidx](B_sens[np.newaxis, :])
            angles[bidx] = angle.ravel()

        return angles
