"""Small Gaussian process and a two-level autoregressive multi-fidelity model.

The high-fidelity response is ``rho * y_low + delta``, with independent
GPs for the low fidelity and the discrepancy. That is the Kennedy-O'Hagan
model: low-fidelity observations change the high-fidelity posterior.
It is not two separately queried single-fidelity GPs.
"""

from __future__ import annotations

import math

import numpy as np

_LENGTHSCALES = (0.35, 0.7, 1.2, 2.0)
_RHO_GRID = tuple(float(value) for value in np.linspace(0.0, 1.5, 16))
_JITTER = (1.0e-8, 1.0e-6, 1.0e-4)


def normal_pdf(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


class GaussianProcess:
    """RBF GP. Lengthscale is chosen on a fixed grid so fits are deterministic."""

    def __init__(self) -> None:
        self.x: np.ndarray | None = None
        self.z: np.ndarray | None = None
        self.y_mean = 0.0
        self.y_std = 1.0
        self.lengthscale = 1.0
        self.alpha: np.ndarray | None = None
        self.chol: np.ndarray | None = None
        self.lml = -math.inf

    def fit(self, x: np.ndarray, y: np.ndarray) -> GaussianProcess:
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float).reshape(-1)
        if len(y) == 0:
            return self
        self.x = x.reshape(len(y), -1)
        self.y_mean = float(np.mean(y))
        spread = float(np.std(y))
        self.y_std = spread if spread > 1.0e-8 else 1.0
        self.z = (y - self.y_mean) / self.y_std
        if len(y) == 1:
            self.lengthscale = 1.0
            self.lml = 0.0
            return self
        best: tuple[float, float, np.ndarray, np.ndarray] | None = None
        for lengthscale in _LENGTHSCALES:
            packed = _fit_lengthscale(self.x, self.z, lengthscale)
            if packed is None:
                continue
            lml, chol, alpha = packed
            if best is None or lml > best[0]:
                best = (lml, lengthscale, chol, alpha)
        if best is None:
            self.lengthscale = 1.0
            self.lml = -math.inf
            return self
        self.lml, self.lengthscale, self.chol, self.alpha = best
        return self

    def predict(self, x_new: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x_new = np.atleast_2d(np.asarray(x_new, dtype=float))
        if self.x is None or self.z is None or len(self.z) == 0:
            return np.zeros(len(x_new)), np.ones(len(x_new))
        if len(self.z) == 1:
            distance = np.sum(((x_new - self.x[0]) / self.lengthscale) ** 2, axis=1)
            kernel = np.exp(-0.5 * distance)
            mean = self.y_mean + self.y_std * self.z[0] * kernel
            var = (self.y_std**2) * np.maximum(1.0 - kernel**2, 1.0e-12)
            return mean, var
        if self.chol is None or self.alpha is None:
            return np.full(len(x_new), self.y_mean), np.full(len(x_new), self.y_std**2)
        cross = _rbf(x_new, self.x, self.lengthscale)
        mean_z = cross @ self.alpha
        solved = np.linalg.solve(self.chol, cross.T)
        var_z = np.maximum(1.0 - np.sum(solved**2, axis=0), 1.0e-12)
        return self.y_mean + self.y_std * mean_z, (self.y_std**2) * var_z


class AutoregressiveModel:
    """Kennedy-O'Hagan model aimed at the high-fidelity response."""

    def __init__(self) -> None:
        self.low = GaussianProcess()
        self.delta = GaussianProcess()
        self.rho = 1.0
        self.has_low = False
        self.has_high = False

    def fit(
        self,
        x_low: np.ndarray,
        y_low: np.ndarray,
        x_high: np.ndarray,
        y_high: np.ndarray,
    ) -> AutoregressiveModel:
        y_low = np.asarray(y_low, dtype=float).reshape(-1)
        y_high = np.asarray(y_high, dtype=float).reshape(-1)
        self.has_low = len(y_low) > 0
        self.has_high = len(y_high) > 0
        if self.has_low:
            self.low.fit(np.asarray(x_low, dtype=float), y_low)
        if not self.has_high:
            self.rho = 1.0
            return self
        if not self.has_low:
            self.rho = 0.0
            self.delta.fit(np.asarray(x_high, dtype=float), y_high)
            return self
        mu_low, _ = self.low.predict(np.asarray(x_high, dtype=float))
        best: tuple[float, float, GaussianProcess] | None = None
        for rho in _RHO_GRID:
            residual = y_high - rho * mu_low
            delta = GaussianProcess().fit(np.asarray(x_high, dtype=float), residual)
            score = delta.lml - abs(rho - 1.0) * 1.0e-6
            if best is None or score > best[0]:
                best = (score, rho, delta)
        assert best is not None
        self.rho = best[1]
        self.delta = best[2]
        return self

    def predict_high(self, x_new: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x_new = np.atleast_2d(np.asarray(x_new, dtype=float))
        if self.has_low and not self.has_high:
            mu, var = self.low.predict(x_new)
            return mu, var + self.low.y_std**2
        if self.has_high and not self.has_low:
            return self.delta.predict(x_new)
        if not self.has_low and not self.has_high:
            return np.zeros(len(x_new)), np.ones(len(x_new))
        mu_low, var_low = self.low.predict(x_new)
        mu_delta, var_delta = self.delta.predict(x_new)
        mean = self.rho * mu_low + mu_delta
        var = self.rho**2 * var_low + var_delta
        return mean, np.maximum(var, 1.0e-12)

    def correlation_with_low(self, x_new: np.ndarray) -> np.ndarray:
        """Prior-style correlation of the high response with the low GP."""
        if not self.has_low or not self.has_high:
            return np.zeros(len(np.atleast_2d(x_new)))
        _, var_low = self.low.predict(x_new)
        _, var_high = self.predict_high(x_new)
        corr = self.rho * np.sqrt(np.maximum(var_low, 0.0)) / np.sqrt(var_high)
        return np.clip(corr, 0.0, 1.0)


def _rbf(left: np.ndarray, right: np.ndarray, lengthscale: float) -> np.ndarray:
    scaled = (left[:, None, :] - right[None, :, :]) / lengthscale
    return np.exp(-0.5 * np.sum(scaled**2, axis=2))


def _fit_lengthscale(
    x: np.ndarray,
    z: np.ndarray,
    lengthscale: float,
) -> tuple[float, np.ndarray, np.ndarray] | None:
    gram = _rbf(x, x, lengthscale)
    for jitter in _JITTER:
        kernel = gram + jitter * np.eye(len(z))
        try:
            chol = np.linalg.cholesky(kernel)
        except np.linalg.LinAlgError:
            continue
        alpha = np.linalg.solve(chol.T, np.linalg.solve(chol, z))
        logdet = 2.0 * float(np.sum(np.log(np.diag(chol))))
        lml = -0.5 * float(z @ alpha) - 0.5 * logdet - 0.5 * len(z) * math.log(2.0 * math.pi)
        return lml, chol, alpha
    return None
