"""Leakage-aware walk-forward classification for the ML research strategy."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import talib
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


LOOKBACK = 60
HORIZON = 5
MIN_TRAIN_SAMPLES = 80
REFIT_INTERVAL = 20


def walk_forward_probabilities(bars: pd.DataFrame) -> tuple[np.ndarray, dict[str, Any]]:
    """Return expanding-window OOS probabilities and their held-out diagnostics."""
    features, close = _features(bars)
    size = len(close)
    probabilities = np.full(size, np.nan, dtype=float)
    indexes = np.arange(size)
    label_ends = indexes + HORIZON
    finite_features = np.isfinite(features).all(axis=1)
    valid_close = np.isfinite(close) & (close > 0)
    labeled = (indexes >= LOOKBACK) & (label_ends < size) & valid_close
    labeled &= valid_close[np.minimum(label_ends, size - 1)]
    labels = np.zeros(size, dtype=np.int8)
    labels[labeled] = (close[label_ends[labeled]] > close[indexes[labeled]]).astype(np.int8)
    first_candidate = LOOKBACK
    start = first_candidate
    max_training_samples = 0

    # Each block is predicted by a model trained only on labels already observable at block start.
    while start < size:
        train_indexes = indexes[labeled & finite_features & (label_ends < start)]
        if len(train_indexes) < MIN_TRAIN_SAMPLES:
            start += REFIT_INTERVAL
            continue
        if np.unique(labels[train_indexes]).size < 2:
            start += REFIT_INTERVAL
            continue
        estimator = _estimator()
        estimator.fit(features[train_indexes], labels[train_indexes])
        max_training_samples = max(max_training_samples, len(train_indexes))
        end = min(start + REFIT_INTERVAL, size)
        predict_indexes = indexes[(indexes >= start) & (indexes < end) & finite_features]
        if predict_indexes.size:
            probabilities[predict_indexes] = estimator.predict_proba(features[predict_indexes])[:, 1]
        start = end

    evaluation_indexes = indexes[np.isfinite(probabilities) & labeled & finite_features]
    if not evaluation_indexes.size:
        valid_labeled_count = int(np.sum(labeled & finite_features))
        if valid_labeled_count < MIN_TRAIN_SAMPLES + LOOKBACK + HORIZON:
            raise ValueError("有效日线样本不足，至少需要约 160 根有效K线才能进行样本外训练")
        raise ValueError("特征无效或训练标签类别不足，无法生成样本外预测")
    actual = labels[evaluation_indexes]
    predicted = probabilities[evaluation_indexes]
    diagnostics = {
        "horizonBars": HORIZON,
        "trainingSamples": max_training_samples,
        "outOfSampleSamples": int(evaluation_indexes.size),
        "outOfSampleStart": bars.iloc[int(evaluation_indexes[0])]["datetime"].isoformat(),
        "outOfSampleEnd": bars.iloc[int(evaluation_indexes[-1])]["datetime"].isoformat(),
        "accuracyPct": float(accuracy_score(actual, predicted >= 0.5) * 100),
        "balancedAccuracyPct": float(balanced_accuracy_score(actual, predicted >= 0.5) * 100),
        "brierScore": float(brier_score_loss(actual, predicted)),
        "auc": float(roc_auc_score(actual, predicted)) if np.unique(actual).size == 2 else None,
        "latestUpProbabilityPct": float(probabilities[-1] * 100) if np.isfinite(probabilities[-1]) else None,
        "caveat": "滚动样本外分类结果仅作研究；标签为未来5根日K涨跌，重叠标签及公开行情样本可能影响评估。",
    }
    return probabilities, diagnostics


def _estimator() -> Any:
    """Create a deterministic, scaled logistic classifier for the small daily sample."""
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(C=0.5, class_weight="balanced", max_iter=500, random_state=42),
    )


def _features(bars: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Build point-in-time price, momentum, volatility and volume features."""
    close = bars["close"].to_numpy(dtype=float)
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    volume = bars["volume"].to_numpy(dtype=float)
    sma20 = talib.SMA(close, timeperiod=20)
    sma60 = talib.SMA(close, timeperiod=LOOKBACK)
    macd = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)[2]
    rsi = talib.RSI(close, timeperiod=14)
    atr = talib.ATR(high, low, close, timeperiod=20)
    log_volume = np.log1p(np.maximum(volume, 0))
    volume_mean = talib.SMA(log_volume, timeperiod=20)
    close_1 = np.roll(close, 1)
    close_5 = np.roll(close, 5)
    close_20 = np.roll(close, 20)
    close_1[:1] = np.nan
    close_5[:5] = np.nan
    close_20[:20] = np.nan
    values = np.column_stack([
        _safe_ratio(close, close_1) - 1,
        _safe_ratio(close, close_5) - 1,
        _safe_ratio(close, close_20) - 1,
        _safe_ratio(close, sma20) - 1,
        _safe_ratio(close, sma60) - 1,
        _safe_ratio(macd, close),
        rsi / 100,
        _safe_ratio(atr, close),
        log_volume - volume_mean,
    ])
    return values, close


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """Divide only finite values by nonzero denominators, leaving invalid bars missing."""
    result = np.full(numerator.shape, np.nan, dtype=float)
    valid = np.isfinite(numerator) & np.isfinite(denominator) & (denominator != 0)
    np.divide(numerator, denominator, out=result, where=valid)
    return result
