from collections import deque
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Optional, Sequence
import numpy as np

from clone_client.state_store.proto.state_store_pb2 import MagneticSensor
from scipy import interpolate


B_OFFSETS = [
     [
        [-1.328, 0.318, 1.198],
        [0.229, 0.198, 2.308],
        [1.143, 0.264, 1.840],
        [-0.534, -0.176, -0.185]
    ], [
        [2.524, 0.067, 1.472],
        [2.256, 0.033, 1.407],
        [2.288, 0.045, 1.429],
        [-0.129, 0.367, 0.086]
    ], [
        [3.599, 0.000, -0.745],
        [3.861, 0.046, -0.745],
        [3.022, 0.034, -0.745],
        [0.367, 0.376, -1.322]
    ]
]


FH3D04_XY_MV_PER_MT = 54.0  # mV / mT for x and y axes
FH3D04_Z_MV_PER_MT = 94.0  # mv / mT for z axis

FH3D04_TEMP_DIGIT2CELS = 0.072484471  # deg C / digit

FH3D04_BASIC_GAIN_XY = 128
FH3D04_BASIC_GAIN_Z = 64
FH3D04_BASIC_DEC_LEN = 512
FH3D04_BASIC_SUPPLY = 2.6



def remap_pixels(pixels: list[MagneticSensor.MagneticPixel]) -> list[MagneticSensor.MagneticPixel]:
    return [
        pixels[3],
        pixels[0],
        pixels[2],
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

    def calculate_sensor(self, x: float, y: float, z: float, temperature: float) -> tuple[float, np.ndarray]:
        t, t_pp = self._calculate_temp(temperature)
        pixel_bs = [
            self._calculate_teslas_xy(x, t_pp),
            self._calculate_teslas_xy(y, t_pp),
            self._calculate_teslas_z(z, t_pp),
        ]

        return t, np.array(pixel_bs)
    

class Interpol:
    def __init__(self, mapping_path: str) -> None:
        mapping = self._load_mapping(Path(mapping_path))

        dip_map = mapping[0]
        pip_map = mapping[1]
        mcp_map = mapping[2]

        self._dip_interpol = interpolate.RBFInterpolator(
            np.array([B for _, B in dip_map]),
            [angle for angle, _ in dip_map],
            kernel="linear",
        )

        self._pip_interpol = interpolate.RBFInterpolator(
            np.array([B for _, B in pip_map]),
            [angle for angle, _ in pip_map],
            kernel="linear",
        )

        self._mcp_interpol = interpolate.RBFInterpolator(
            np.array([B for _, B in mcp_map]),
            [angle for angle, _ in mcp_map],
            kernel="linear",
        )

        self._filter_outliers_population = 50
        self._filt_samples = deque(maxlen=self._filter_outliers_population)
        self.estimator = NaiveMappingEstimatorBase(
            NaiveMappingEstimatorBase.Config(
                dec_len=FH3D04_BASIC_DEC_LEN,
                gain_xy=FH3D04_BASIC_GAIN_XY,
                gain_z=FH3D04_BASIC_GAIN_Z,
                supply=FH3D04_BASIC_SUPPLY,
            )
        )


    def _load_mapping(self, map_path: Path) -> dict[int, Optional[list[tuple[float, np.ndarray]]]]:
        """Returns jnt_nr -> (angles, B)"""
        with map_path.open("r") as fp:
            map_ = json.load(fp)

        return {
            int(snsr_nr): (
                [(float(ang), np.array(B).ravel()) for ang, B in snsr_map.items()]
                if snsr_map is not None
                else None
            )
            for snsr_nr, snsr_map in map_.items()
        }
    

    def filter_outliers(
        self, arr_flat: np.ndarray, sig_mul=3.0
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
    
    def get_angles(self, sensors: Sequence[MagneticSensor], skip_filtering: bool = True) -> Optional[np.ndarray]:
        """
        Returns angles in degrees for each sensor.
        """

        interpolators = [
            self._dip_interpol,
            self._pip_interpol,
            self._mcp_interpol,
        ]

        angles = np.zeros((3, 1), dtype=np.float32)
        for idx, sensor in enumerate(sensors):
            B = np.zeros((4, 3), dtype=np.float32)
            for pidx, pixel in enumerate(remap_pixels(sensor.pixels)):
                _, B_pixel = self.estimator.calculate_sensor(
                    pixel.x, pixel.y, pixel.z, sensor.temperature
                )

                B[pidx] = B_pixel

            B -= B_OFFSETS[idx] 
            B_flat_filtered = B.flatten()
            if not skip_filtering:
                B_flat_filtered = self.filter_outliers(B_flat_filtered)
                if B_flat_filtered is None:
                    continue

            B_flat_filtered = B_flat_filtered.reshape(1, -1)
            angle = interpolators[idx](B_flat_filtered)
            angles[idx] = angle

        return angles