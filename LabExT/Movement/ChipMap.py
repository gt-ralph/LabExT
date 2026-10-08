#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2022  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY;
for details see LICENSE file.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, List, Optional, Tuple, Type

import numpy as np

from LabExT.Movement.Transformations import (
    ChipCoordinate,
    StageCoordinate,
    Transformation,
    TransformationError,
    assert_valid_transformation,
)

if TYPE_CHECKING:
    from LabExT.Movement.Transformations import AxesRotation
    from LabExT.Wafer.Chip import Chip
    from LabExT.Wafer.Device import Device


logger = logging.getLogger()


class MapLevel(IntEnum):
    """
    Complexity of the global in-plane (x/y) model of a chip map.

    OFFSET: chip axes parallel to stage axes (after the axes rotation), translation only.
    RIGID:  in-plane rotation (chip yaw) + translation.
    AFFINE: per-axis scale and axis non-orthogonality on top of RIGID, i.e. stage travel errors.
    """
    OFFSET = 0
    RIGID = 1
    AFFINE = 2


@dataclass
class ChipMapPoint:
    """
    One recorded stage position for a known chip coordinate.

    Points are never deleted by re-measurements: an accepted re-measurement of the same device
    marks the older point as superseded, and a gated-out point is kept with accepted=False,
    so the dumped map is a complete record of the session.
    """
    chip: np.ndarray
    stage: np.ndarray
    device_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    source: str = "manual"
    accepted: bool = True
    reject_reason: Optional[str] = None
    superseded: bool = False
    predicted_stage: Optional[np.ndarray] = None
    sigma_um: Optional[float] = None
    sfp_power_dbm: Optional[float] = None
    device: Optional["Device"] = field(default=None, repr=False, compare=False)

    @property
    def is_active(self) -> bool:
        """
        Returns True if the point takes part in the fit.
        """
        return self.accepted and not self.superseded

    @property
    def correction_um(self) -> Optional[float]:
        """
        Distance between where the map predicted the device and where it was found.
        """
        if self.predicted_stage is None:
            return None
        return float(np.linalg.norm(self.stage - self.predicted_stage))

    def dump(self) -> dict:
        return {
            "device_id": self.device_id,
            "chip": self.chip.tolist(),
            "stage": self.stage.tolist(),
            "predicted_stage": None if self.predicted_stage is None else self.predicted_stage.tolist(),
            "sigma_um": self.sigma_um,
            "correction_um": self.correction_um,
            "sfp_power_dbm": self.sfp_power_dbm,
            "timestamp": self.timestamp,
            "source": self.source,
            "accepted": self.accepted,
            "reject_reason": self.reject_reason,
            "superseded": self.superseded,
        }

    @classmethod
    def load(cls, data: dict, chip: Type[Chip] = None) -> ChipMapPoint:
        device_id = data.get("device_id")
        device = None
        if chip is not None and device_id is not None:
            device = chip.devices.get(str(device_id))

        predicted = data.get("predicted_stage")
        return cls(
            chip=np.asarray(data["chip"], dtype=float),
            stage=np.asarray(data["stage"], dtype=float),
            device_id=None if device_id is None else str(device_id),
            timestamp=float(data.get("timestamp", time.time())),
            source=data.get("source", "manual"),
            accepted=bool(data.get("accepted", True)),
            reject_reason=data.get("reject_reason"),
            superseded=bool(data.get("superseded", False)),
            predicted_stage=None if predicted is None else np.asarray(predicted, dtype=float),
            sigma_um=data.get("sigma_um"),
            sfp_power_dbm=data.get("sfp_power_dbm"),
            device=device)


@dataclass
class _GlobalFit:
    """
    Parameters of the global model, expressed in the chip-aligned frame q = R_axes^T * stage:
        q_xy = A @ u + b
        q_z  = chip_z + z0 + g . (u - u0)
    with u = chip (x, y).
    """
    level: MapLevel
    A: np.ndarray
    b: np.ndarray
    z0: float
    g: np.ndarray
    u0: np.ndarray

    def predict_q(self, u: np.ndarray, chip_z: float) -> np.ndarray:
        q_xy = self.A @ u + self.b
        q_z = chip_z + self.z0 + float(self.g @ (u - self.u0))
        return np.array([q_xy[0], q_xy[1], q_z])


class ChipMapTransformation(Transformation):
    """
    Chip-to-stage transformation that is built up point by point while measuring.

    Starting from a single aligned device, every added point (manual pairing or accepted
    search-for-peak result) refines the map. The model complexity grows with the data:

    - x/y (in chip plane): OFFSET with 1 point, RIGID (chip yaw) once 2 points are far enough
      apart, AFFINE (stage scale / non-orthogonal axes) once enough well-spread points exist
      and leave-one-out cross-validation shows it predicts better than RIGID.
    - z: a plane z = z0 + g . (u - u0). Directions in which the points do not spread
      (e.g. all devices in one row) get no slope instead of an arbitrary one.
    - Local residual correction: what the global model cannot explain at measured devices
      (non-linear stage travel, chip bow, drift) is interpolated to nearby targets with a
      Gaussian kernel, so close neighbours dominate.

    Every prediction comes with an uncertainty estimate sigma (um), used to size the
    search-for-peak radius and to gate out implausible search results.

    The axes rotation is applied first (stage = R_axes * q), so the model only has to learn the
    small deviations from the user-defined axes mapping. The map fits lazily and refits when
    the axes rotation is changed.
    """

    # noise floor of a single recorded position (um)
    POINT_SIGMA_UM = 1.0
    # assumed chip yaw uncertainty while only a translation is known (deg)
    YAW_PRIOR_DEG = 1.0
    # assumed chip tilt uncertainty while the z plane is not fully determined (deg)
    TILT_PRIOR_DEG = 0.3
    # minimum distance between points before a rotation is fitted (um)
    MIN_BASELINE_UM = 50.0
    # minimum RMS spread perpendicular to the main direction before 2D terms are fitted (um)
    MIN_SPREAD_UM = 50.0
    # minimum number of points before the affine model is considered
    AFFINE_MIN_POINTS = 6
    # affine must lower the leave-one-out error by this factor to be chosen over rigid
    AFFINE_LOO_IMPROVEMENT = 0.9
    # length scale of the local residual correction (um)
    RESIDUAL_LENGTH_UM = 250.0
    # kernel weight that pulls the residual correction to zero away from points
    RESIDUAL_SHRINK = 0.1
    # outlier gate: reject if |found - predicted| > max(GATE_K * sigma, GATE_FLOOR_UM)
    GATE_K = 3.0
    GATE_FLOOR_UM = 5.0
    # the gate is only applied once the map has this many active points
    GATE_MIN_POINTS = 3
    # robust (Huber) fitting from this many active points on, so a bad point that was accepted
    # before the gate was active cannot drag the whole map
    ROBUST_MIN_POINTS = 8
    # Huber threshold in robust standard deviations of the residuals
    HUBER_K = 2.5
    ROBUST_ITERATIONS = 4

    SETTINGS = (
        "POINT_SIGMA_UM", "YAW_PRIOR_DEG", "TILT_PRIOR_DEG", "MIN_BASELINE_UM", "MIN_SPREAD_UM",
        "AFFINE_MIN_POINTS", "AFFINE_LOO_IMPROVEMENT", "RESIDUAL_LENGTH_UM",
        "RESIDUAL_SHRINK", "GATE_K", "GATE_FLOOR_UM", "GATE_MIN_POINTS",
        "ROBUST_MIN_POINTS", "HUBER_K", "ROBUST_ITERATIONS")

    @classmethod
    def load(
        cls,
        data: dict,
        axes_rotation: Type[AxesRotation],
        chip: Type[Chip] = None
    ) -> ChipMapTransformation:
        """
        Creates a chip map from dumped data.

        Parameters
        ----------
        data : dict
            Output of `dump()`.
        axes_rotation : AxesRotation
            Axes rotation of the calibration the map belongs to.
        chip : Chip = None
            If given, points are linked to the chip's devices by their ID.
        """
        try:
            settings = {k: v for k, v in (data.get("settings") or {}).items() if k in cls.SETTINGS}
            chip_map = cls(
                axes_rotation,
                drift_tau_s=data.get("drift_tau_s"),
                residual_correction=data.get("residual_correction", True),
                max_level=MapLevel[data.get("max_level", MapLevel.AFFINE.name)],
                **settings)
            chip_map.points = [ChipMapPoint.load(p, chip=chip) for p in data.get("points", [])]
        except (KeyError, TypeError, ValueError) as err:
            raise TransformationError(f"Cannot load chip map: {err!r}")

        chip_map._invalidate()
        return chip_map

    def __init__(
        self,
        axes_rotation: Type[AxesRotation],
        drift_tau_s: Optional[float] = None,
        residual_correction: bool = True,
        max_level: MapLevel = MapLevel.AFFINE,
        **settings
    ) -> None:
        """
        Parameters
        ----------
        axes_rotation : AxesRotation
            Axes rotation of the calibration. Must be valid.
        drift_tau_s : float = None
            If set, points are weighted by exp(-age / drift_tau_s), age relative to the newest
            point, so the fit follows slow drift. None weights all points equally.
        residual_correction : bool = True
            Whether to apply the local residual correction.
        max_level : MapLevel = MapLevel.AFFINE
            Highest x/y model level the map may choose.
        settings
            Overrides of the class-level tuning constants (see SETTINGS).
        """
        self.axes_rotation = axes_rotation
        if not self.axes_rotation.is_valid:
            raise RuntimeError("The given axes rotation is invalid.")

        for key, value in settings.items():
            if key not in self.SETTINGS:
                raise ValueError(f"Unknown chip map setting {key!r}")
            setattr(self, key, value)

        self.drift_tau_s = drift_tau_s
        self.residual_correction = residual_correction
        self.max_level = MapLevel(max_level)

        self.initialize()

    def initialize(self) -> None:
        """
        Removes all points.
        """
        self.points: List[ChipMapPoint] = []
        self._invalidate()

    def __str__(self) -> str:
        n = len(self.active_points)
        if n == 0:
            return "No chip map points set"
        fit = self._get_fit()
        return f"Chip map with {n} point{'s' if n != 1 else ''} ({fit.level.name.lower()})"

    #
    #   Points
    #

    @property
    def active_points(self) -> List[ChipMapPoint]:
        return [p for p in self.points if p.is_active]

    @property
    def is_valid(self) -> bool:
        """
        Returns True if the map can transform, i.e. it has at least one active point.
        """
        return self.axes_rotation.is_valid and len(self.active_points) >= 1

    @property
    def is_fully_constrained(self) -> bool:
        """
        Returns True if the map determines the full chip pose: at least 3 active points that
        spread in both in-plane directions, so yaw and both tilt directions are fitted.
        """
        active = self.active_points
        if len(active) < 3:
            return False
        return self._minor_spread(self._chip_xy(active)) >= self.MIN_SPREAD_UM

    def add_point(
        self,
        chip_coordinate: Type[ChipCoordinate],
        stage_coordinate: Type[StageCoordinate],
        device: Type[Device] = None,
        source: str = "manual",
        gate: bool = False,
        sfp_power_dbm: float = None,
        timestamp: float = None
    ) -> ChipMapPoint:
        """
        Adds a recorded stage position for a chip coordinate and refits the map.

        Parameters
        ----------
        chip_coordinate : ChipCoordinate
            Designed position (e.g. the device's input coordinate).
        stage_coordinate : StageCoordinate
            Stage position at which the device was actually found.
        device : Device = None
            Device the point belongs to. An accepted point replaces the device's earlier point.
        source : str = "manual"
            Where the point came from, e.g. "wizard" or "sfp". Stored only.
        gate : bool = False
            If True and the map has at least GATE_MIN_POINTS active points, a point that lies
            further from the prediction than max(GATE_K * sigma, GATE_FLOOR_UM) is stored as
            rejected and not fitted.
        sfp_power_dbm : float = None
            Power found by the search for peak. Stored only.
        timestamp : float = None
            Seconds since epoch; defaults to now.

        Returns
        -------
        ChipMapPoint
            The stored point. Check `accepted` / `reject_reason`.
        """
        if chip_coordinate is None or stage_coordinate is None:
            raise ValueError("Chip map points need a chip and a stage coordinate.")

        point = ChipMapPoint(
            chip=np.asarray(chip_coordinate.to_numpy(), dtype=float),
            stage=np.asarray(stage_coordinate.to_numpy(), dtype=float),
            device_id=None if device is None else str(device.id),
            timestamp=time.time() if timestamp is None else float(timestamp),
            source=source,
            sfp_power_dbm=sfp_power_dbm,
            device=device)

        if self.is_valid:
            predicted, sigma = self.predict(chip_coordinate)
            point.predicted_stage = predicted.to_numpy()
            point.sigma_um = sigma

            if gate and len(self.active_points) >= self.GATE_MIN_POINTS:
                limit = max(self.GATE_K * sigma, self.GATE_FLOOR_UM)
                if point.correction_um > limit:
                    point.accepted = False
                    point.reject_reason = (
                        f"found {point.correction_um:.1f} um from the predicted position, "
                        f"more than the allowed {limit:.1f} um ({self.GATE_K:g} sigma, sigma = {sigma:.1f} um)")

        if point.accepted and point.device_id is not None:
            for p in self.active_points:
                if p.device_id == point.device_id:
                    p.superseded = True

        self.points.append(point)
        self._invalidate()
        return point

    def remove_points_for_device(self, device_id: str) -> bool:
        """
        Removes all points of a device. Returns True if any point was removed.
        """
        device_id = str(device_id)
        before = len(self.points)
        self.points = [p for p in self.points if p.device_id != device_id]
        self._invalidate()
        return len(self.points) != before

    #
    #   Transformation interface
    #

    @assert_valid_transformation
    def chip_to_stage(
            self,
            chip_coordinate: Type[ChipCoordinate]) -> Type[StageCoordinate]:
        """
        Transforms a chip coordinate into a stage coordinate.
        """
        return self.predict(chip_coordinate)[0]

    @assert_valid_transformation
    def stage_to_chip(
            self,
            stage_coordinate: Type[StageCoordinate]) -> Type[ChipCoordinate]:
        """
        Transforms a stage coordinate into a chip coordinate.

        Inverts the global model and then iterates to account for the local residual correction.
        """
        fit = self._get_fit()
        q = self._to_q(stage_coordinate.to_numpy())
        A_inv = np.linalg.inv(fit.A)

        correction = np.zeros(3)
        u = A_inv @ (q[:2] - fit.b)
        for _ in range(10):
            correction = self._residual_correction(u)
            u_next = A_inv @ (q[:2] - correction[:2] - fit.b)
            if np.linalg.norm(u_next - u) < 1e-9:
                u = u_next
                break
            u = u_next

        chip_z = q[2] - correction[2] - fit.z0 - float(fit.g @ (u - fit.u0))
        return ChipCoordinate(float(u[0]), float(u[1]), float(chip_z))

    @assert_valid_transformation
    def predict(
            self,
            chip_coordinate: Type[ChipCoordinate]) -> Tuple[Type[StageCoordinate], float]:
        """
        Returns the predicted stage coordinate and its uncertainty sigma (um).
        """
        fit = self._get_fit()
        chip = chip_coordinate.to_numpy().astype(float)
        u = chip[:2]

        q = fit.predict_q(u, chip[2]) + self._residual_correction(u)
        stage = StageCoordinate.from_numpy(self.axes_rotation.matrix @ q)
        return stage, self._sigma(u)

    #
    #   Diagnostics
    #

    @property
    def level(self) -> Optional[MapLevel]:
        if not self.is_valid:
            return None
        return self._get_fit().level

    def get_point_residuals_um(self) -> List[Tuple[ChipMapPoint, float]]:
        """
        Distance between each active point and the global model (without local correction).
        """
        if not self.is_valid:
            return []
        fit = self._get_fit()
        return [
            (p, float(np.linalg.norm(self._to_q(p.stage) - fit.predict_q(p.chip[:2], p.chip[2]))))
            for p in self.active_points]

    def get_leave_one_out_rmse_um(self, level: MapLevel = None) -> Optional[float]:
        """
        Leave-one-out RMSE of the global model at the given (default: current) level.
        None if there are too few points to refit without one of them.
        """
        if not self.is_valid:
            return None
        level = self._get_fit().level if level is None else level
        return self._active_loo(level)

    def get_diagnostics(self) -> dict:
        """
        Summary of the current fit, for logging and the dumped map.
        """
        active = self.active_points
        diagnostics = {
            "level": None,
            "active_points": len(active),
            "rejected_points": sum(1 for p in self.points if not p.accepted),
        }
        if not self.is_valid:
            return diagnostics

        fit = self._get_fit()
        U = self._chip_xy(active)
        col_x, col_y = fit.A[:, 0], fit.A[:, 1]
        cos_xy = float(col_x @ col_y / (np.linalg.norm(col_x) * np.linalg.norm(col_y)))
        slope = float(np.linalg.norm(fit.g))

        diagnostics.update({
            "level": fit.level.name,
            "yaw_deg": float(np.degrees(np.arctan2(fit.A[1, 0], fit.A[0, 0]))),
            "scale_x_ppm": float((np.linalg.norm(col_x) - 1) * 1e6),
            "scale_y_ppm": float((np.linalg.norm(col_y) - 1) * 1e6),
            "skew_deg": float(90.0 - np.degrees(np.arccos(np.clip(cos_xy, -1, 1)))),
            "tilt_deg": float(np.degrees(np.arctan(slope))),
            "tilt_percent": slope * 100,
            "baseline_um": self._baseline(U),
            "minor_spread_um": self._minor_spread(U),
            "loo_rmse_um": self._active_loo(fit.level),
            "fully_constrained": self.is_fully_constrained,
            "downweighted_points": int(np.sum(self._robust_weights < 1.0)),
        })
        return diagnostics

    def dump(self) -> dict:
        return {
            "settings": {k: getattr(self, k) for k in self.SETTINGS},
            "drift_tau_s": self.drift_tau_s,
            "residual_correction": self.residual_correction,
            "max_level": self.max_level.name,
            "diagnostics": self.get_diagnostics(),
            "points": [p.dump() for p in self.points],
        }

    #
    #   Fitting
    #

    def _invalidate(self) -> None:
        self._fit: Optional[_GlobalFit] = None
        self._fit_axes = None
        self._residuals = None
        self._robust_weights = None
        self._loo_cache = {}

    def _get_fit(self) -> _GlobalFit:
        """
        Returns the current global fit, refitting if points or the axes rotation changed.
        """
        axes = self.axes_rotation.matrix
        if self._fit is None or self._fit_axes is None or not np.array_equal(self._fit_axes, axes):
            active = self.active_points
            if not active:
                raise TransformationError("Chip map has no points.")
            self._fit_axes = axes.copy()
            self._loo_cache = {}
            self._fit = self._fit_level(active, self._select_level(active))
            self._residuals = np.array([
                self._to_q(p.stage) - self._fit.predict_q(p.chip[:2], p.chip[2]) for p in active])
            self._robust_weights = (
                self._huber_weights(active, self._fit) if len(active) >= self.ROBUST_MIN_POINTS
                else np.ones(len(active)))
        return self._fit

    def _to_q(self, stage: np.ndarray) -> np.ndarray:
        # the axes rotation is a signed permutation matrix, so its inverse is its transpose
        return self.axes_rotation.matrix.T @ np.asarray(stage, dtype=float)

    @staticmethod
    def _chip_xy(points: List[ChipMapPoint]) -> np.ndarray:
        return np.array([p.chip[:2] for p in points]).reshape(-1, 2)

    def _weights(self, points: List[ChipMapPoint]) -> np.ndarray:
        if not self.drift_tau_s:
            return np.ones(len(points))
        t = np.array([p.timestamp for p in points])
        return np.exp(-(t.max() - t) / float(self.drift_tau_s))

    @staticmethod
    def _baseline(U: np.ndarray) -> float:
        """
        Largest distance between two points.
        """
        if len(U) < 2:
            return 0.0
        diffs = U[:, None, :] - U[None, :, :]
        return float(np.sqrt((diffs ** 2).sum(axis=-1)).max())

    @staticmethod
    def _minor_spread(U: np.ndarray) -> float:
        """
        RMS spread of the points perpendicular to their main direction.
        """
        if len(U) < 3:
            return 0.0
        s = np.linalg.svd(U - U.mean(axis=0), compute_uv=False)
        return float(s[-1] / np.sqrt(len(U)))

    def _available_levels(self, points: List[ChipMapPoint]) -> List[MapLevel]:
        U = self._chip_xy(points)
        levels = [MapLevel.OFFSET]
        if len(points) >= 2 and self._baseline(U) >= self.MIN_BASELINE_UM:
            levels.append(MapLevel.RIGID)
        if len(points) >= self.AFFINE_MIN_POINTS and self._minor_spread(U) >= self.MIN_SPREAD_UM:
            levels.append(MapLevel.AFFINE)
        return [lvl for lvl in levels if lvl <= self.max_level]

    def _select_level(self, points: List[ChipMapPoint]) -> MapLevel:
        """
        RIGID whenever the points allow a rotation (chip yaw is always physically present),
        AFFINE only if it cross-validates clearly better than RIGID.
        """
        levels = self._available_levels(points)
        level = MapLevel.RIGID if MapLevel.RIGID in levels else MapLevel.OFFSET

        if MapLevel.AFFINE in levels:
            loo_affine = self._active_loo(MapLevel.AFFINE)
            loo_rigid = self._active_loo(level)
            if loo_affine is not None and loo_rigid is not None \
                    and loo_affine < self.AFFINE_LOO_IMPROVEMENT * loo_rigid:
                level = MapLevel.AFFINE

        return level

    def _fit_level(self, points: List[ChipMapPoint], level: MapLevel) -> _GlobalFit:
        """
        Fits the global model; from ROBUST_MIN_POINTS on with iteratively reweighted Huber weights.
        """
        w = self._weights(points)
        fit = self._fit_weighted(points, level, w)
        if len(points) < self.ROBUST_MIN_POINTS:
            return fit
        for _ in range(self.ROBUST_ITERATIONS):
            fit = self._fit_weighted(points, level, w * self._huber_weights(points, fit))
        return fit

    def _huber_weights(self, points: List[ChipMapPoint], fit: _GlobalFit) -> np.ndarray:
        """
        Huber weights of the points' residuals to a fit: 1 inside HUBER_K robust standard
        deviations (but at least HUBER_K * POINT_SIGMA_UM), falling off as 1/residual outside.
        """
        residuals = np.array([
            np.linalg.norm(self._to_q(p.stage) - fit.predict_q(p.chip[:2], p.chip[2])) for p in points])
        scale = max(1.4826 * float(np.median(residuals)), self.POINT_SIGMA_UM)
        threshold = self.HUBER_K * scale
        return np.minimum(1.0, threshold / np.maximum(residuals, 1e-12))

    def _fit_weighted(self, points: List[ChipMapPoint], level: MapLevel, w: np.ndarray) -> _GlobalFit:
        U = self._chip_xy(points)
        Q = np.array([self._to_q(p.stage) for p in points]).reshape(-1, 3)
        chip_z = np.array([p.chip[2] for p in points])
        w_norm = w / w.sum()

        u_mean = w_norm @ U
        q_mean = w_norm @ Q[:, :2]

        if level == MapLevel.OFFSET:
            A = np.identity(2)
        elif level == MapLevel.RIGID:
            # weighted 2D Procrustes: theta maximising sum w * q_c . R(theta) u_c
            Uc, Qc = U - u_mean, Q[:, :2] - q_mean
            sin_term = np.sum(w * (Qc[:, 1] * Uc[:, 0] - Qc[:, 0] * Uc[:, 1]))
            cos_term = np.sum(w * (Qc[:, 0] * Uc[:, 0] + Qc[:, 1] * Uc[:, 1]))
            theta = np.arctan2(sin_term, cos_term)
            A = np.array([[np.cos(theta), -np.sin(theta)],
                          [np.sin(theta), np.cos(theta)]])
        elif level == MapLevel.AFFINE:
            X = np.hstack([U - u_mean, np.ones((len(U), 1))])
            sw = np.sqrt(w)[:, None]
            coef, *_ = np.linalg.lstsq(X * sw, (Q[:, :2] - q_mean) * sw, rcond=None)
            A = coef[:2].T
            q_mean = q_mean + coef[2]
        else:
            raise ValueError(f"Unknown chip map level {level}")

        b = q_mean - A @ u_mean

        # z plane on the offsets dz = q_z - chip_z; slopes only along directions the points
        # actually spread in, otherwise the tilt would be arbitrary
        dz = Q[:, 2] - chip_z
        z0 = float(w_norm @ dz)
        g = np.zeros(2)
        if len(points) >= 2:
            sw = np.sqrt(w_norm)
            Uc = (U - u_mean) * sw[:, None]
            _, s, Vt = np.linalg.svd(Uc, full_matrices=False)
            rhs = (dz - z0) * sw
            for k, s_k in enumerate(s):
                # s_k is the weighted RMS spread along direction Vt[k]; two points at the
                # minimum baseline have an RMS spread of half the baseline
                if s_k >= (self.MIN_BASELINE_UM / 2 if k == 0 else self.MIN_SPREAD_UM):
                    g += Vt[k] * (Vt[k] @ (Uc.T @ rhs)) / s_k ** 2

        return _GlobalFit(level=level, A=A, b=b, z0=z0, g=g, u0=u_mean)

    def _leave_one_out_rmse(self, points: List[ChipMapPoint], level: MapLevel) -> Optional[float]:
        if len(points) < 3:
            return None
        # dropping one of many points cannot make a level unavailable, so only small sets are
        # checked per held-out point (the check is O(n^2))
        check_each = len(points) <= 2 * self.AFFINE_MIN_POINTS
        if not check_each and level not in self._available_levels(points):
            return None

        # robust weights are taken once from the fit on all points: refitting robustly per fold
        # would multiply the cost, and weighting the errors keeps a down-weighted outlier from
        # inflating the error (and with it sigma and the gate)
        w = self._weights(points)
        robust = np.ones(len(points))
        if len(points) >= self.ROBUST_MIN_POINTS:
            robust = self._huber_weights(points, self._fit_level(points, level))

        errors = []
        for i, held_out in enumerate(points):
            rest = points[:i] + points[i + 1:]
            if check_each and level not in self._available_levels(rest):
                return None
            keep = np.arange(len(points)) != i
            fit = self._fit_weighted(rest, level, (w * robust)[keep])
            predicted = fit.predict_q(held_out.chip[:2], held_out.chip[2])
            errors.append(np.linalg.norm(self._to_q(held_out.stage) - predicted))
        return float(np.sqrt(np.average(np.square(errors), weights=robust)))

    def _active_loo(self, level: MapLevel) -> Optional[float]:
        # cached per fit; invalidated together with the fit
        if level not in self._loo_cache:
            self._loo_cache[level] = self._leave_one_out_rmse(self.active_points, level)
        return self._loo_cache[level]

    def _residual_correction(self, u: np.ndarray) -> np.ndarray:
        if not self.residual_correction:
            return np.zeros(3)
        self._get_fit()
        active = self.active_points
        if len(active) < 2 or self._residuals is None:
            return np.zeros(3)

        U = self._chip_xy(active)
        d2 = np.sum((U - u) ** 2, axis=1)
        k = self._weights(active) * np.exp(-d2 / (2 * self.RESIDUAL_LENGTH_UM ** 2))
        # down-weighted (outlying) points contribute their Huber-clipped residual, so an isolated
        # bad point cannot pull its neighbourhood by more than the Huber threshold
        clipped = self._residuals * self._robust_weights[:, None]
        return (k @ clipped) / (k.sum() + self.RESIDUAL_SHRINK)

    def _sigma(self, u: np.ndarray) -> float:
        """
        Heuristic 1-sigma uncertainty of a prediction at chip position u (um):
            sqrt(sigma_fit^2 + (d * dtheta)^2 [+ (d * tan(TILT_PRIOR_DEG))^2])
        sigma_fit is the leave-one-out RMSE (at least POINT_SIGMA_UM), d the distance to the
        nearest active point and dtheta the yaw uncertainty: YAW_PRIOR_DEG while only an offset
        is known, otherwise derived from the fit error over the point baseline. The tilt term
        applies until the map is fully constrained, since z slopes are only fitted along
        directions the points spread in.
        """
        fit = self._get_fit()
        active = self.active_points
        U = self._chip_xy(active)

        sigma_fit = self.POINT_SIGMA_UM
        loo = self._active_loo(fit.level)
        if loo is not None:
            sigma_fit = max(sigma_fit, loo)

        if fit.level == MapLevel.OFFSET:
            dtheta = np.radians(self.YAW_PRIOR_DEG)
        else:
            dtheta = sigma_fit * np.sqrt(2) / max(self._baseline(U), 1.0)

        d = float(np.sqrt(np.min(np.sum((U - u) ** 2, axis=1))))
        variance = sigma_fit ** 2 + (d * dtheta) ** 2
        if not self.is_fully_constrained:
            variance += (d * np.tan(np.radians(self.TILT_PRIOR_DEG))) ** 2
        return float(np.sqrt(variance))
